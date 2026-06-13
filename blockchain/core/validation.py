from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from blockchain.core.codec import (
    decode_coinbase_data,
    decode_transfer_data,
    pack_block,
    pack_header,
    pack_timestamp_for_signing,
)
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import (
    HEADER_SIZE,
    MAGIC_COINBASE,
    MAGIC_UTXO_TRANSFER,
    Block,
    BlockHeader,
    Outpoint,
    Result,
    Transaction,
    UTXO,
)
from blockchain.core.hashing import block_hash, txs_hash
from blockchain.core.pow import has_leading_zero_bits

MAX_BLOCK_BYTES = 1_048_576

VerifySignature = Callable[[bytes, bytes, bytes], bool]


class UTXOReader(Protocol):
    def get(self, outpoint: Outpoint) -> UTXO | None:
        ...

    def is_unspent(self, outpoint: Outpoint) -> bool:
        ...


class TxClass(StrEnum):
    COINBASE = "coinbase"
    TRANSFER = "transfer"
    DATA_CARRIER = "data-carrier"


def _ok() -> Result:
    return Result(True, "")


def _err(reason: str) -> Result:
    return Result(False, reason)


def classify_tx(tx: Transaction) -> TxClass:
    data = tx.data
    if data.startswith(MAGIC_COINBASE):
        return TxClass.COINBASE
    if data.startswith(MAGIC_UTXO_TRANSFER):
        try:
            decode_transfer_data(data)
        except ValueError:
            return TxClass.DATA_CARRIER
        return TxClass.TRANSFER
    return TxClass.DATA_CARRIER


def signing_message(tx: Transaction) -> bytes:
    return tx.sender_key + tx.data + pack_timestamp_for_signing(tx.timestamp)


def validate_header_size(header: BlockHeader) -> Result:
    packed = pack_header(header)
    if len(packed) != HEADER_SIZE:
        return _err(f"header must be {HEADER_SIZE} bytes, got {len(packed)}")
    return _ok()


def validate_pow(header: BlockHeader, *, genesis: bool) -> Result:
    if genesis:
        return _ok()
    digest = block_hash(header)
    if not has_leading_zero_bits(digest, header.difficulty):
        return _err("block hash does not meet difficulty target")
    return _ok()


def validate_header_difficulty(header: BlockHeader, params: ConsensusParams) -> Result:
    if header.difficulty != params.difficulty_bits:
        return _err("header difficulty does not match consensus params")
    return _ok()


def validate_txs_hash(block: Block) -> Result:
    expected = txs_hash(block.transactions)
    if block.header.txs_hash != expected:
        return _err("txs_hash mismatch")
    return _ok()


def validate_transaction_signature(
    tx: Transaction,
    verify: VerifySignature,
) -> Result:
    message = signing_message(tx)
    if not verify(tx.sender_key, message, tx.signature):
        return _err("invalid transaction signature")
    return _ok()


def _genesis_signature_exempt(
    tx: Transaction,
    tx_index: int,
    height: int,
    params: ConsensusParams,
) -> bool:
    return (
        height == 0
        and tx_index == 0
        and tx.signature == params.genesis_coinbase_signature
    )


def validate_coinbase_structure(tx: Transaction, block_height: int) -> Result:
    if classify_tx(tx) != TxClass.COINBASE:
        return _err("transaction is not a coinbase")
    try:
        coinbase = decode_coinbase_data(tx.data)
    except ValueError:
        return _err("invalid coinbase data encoding")
    if coinbase.height != block_height:
        return _err("coinbase height does not match block height")
    return _ok()


def validate_block_coinbase_rules(block: Block, block_height: int) -> Result:
    if not block.transactions:
        return _err("block has no transactions")
    first = block.transactions[0]
    coinbase_result = validate_coinbase_structure(first, block_height)
    if not coinbase_result.ok:
        return coinbase_result
    coinbase_count = sum(1 for tx in block.transactions if classify_tx(tx) == TxClass.COINBASE)
    if coinbase_count != 1:
        return _err("block must contain exactly one coinbase transaction")
    return _ok()


def validate_block_size(block: Block, max_bytes: int = MAX_BLOCK_BYTES) -> Result:
    if len(pack_block(block)) > max_bytes:
        return _err("block exceeds maximum serialized size")
    return _ok()


def validate_prev_hash_link(header: BlockHeader, parent_block_hash: bytes) -> Result:
    if header.prev_hash != parent_block_hash:
        return _err("prev_hash does not link to parent")
    return _ok()


def validate_block_timestamp(
    header: BlockHeader,
    *,
    parent_timestamp: int | None,
    now: int,
    tolerance_seconds: int,
    genesis: bool,
) -> Result:
    if genesis:
        return _ok()
    if parent_timestamp is None:
        return _err("missing parent timestamp")
    if header.timestamp <= parent_timestamp:
        return _err("timestamp must be greater than parent")
    if header.timestamp > now + tolerance_seconds:
        return _err("timestamp too far in the future")
    return _ok()


def transfer_fee(tx: Transaction, utxo_view: UTXOReader) -> int:
    if classify_tx(tx) != TxClass.TRANSFER:
        return 0
    transfer = decode_transfer_data(tx.data)
    input_total = 0
    for inp in transfer.inputs:
        outpoint = Outpoint(txid=inp.prev_txid, index=inp.output_index)
        utxo = utxo_view.get(outpoint)
        if utxo is None:
            raise ValueError("transfer input references missing UTXO")
        input_total += utxo.amount
    output_total = sum(output.amount for output in transfer.outputs)
    return input_total - output_total


def validate_coinbase_value(
    block: Block,
    height: int,
    params: ConsensusParams,
    utxo_view: UTXOReader,
) -> Result:
    if height == 0:
        return _ok()
    coinbase = block.transactions[0]
    coinbase_data = decode_coinbase_data(coinbase.data)
    output_total = sum(output.amount for output in coinbase_data.outputs)
    fees = 0
    for tx in block.transactions[1:]:
        if classify_tx(tx) != TxClass.TRANSFER:
            continue
        try:
            fees += transfer_fee(tx, utxo_view)
        except ValueError:
            return _err("transfer input references missing UTXO")
    expected = params.reward(height) + fees
    if output_total != expected:
        return _err("coinbase output amount mismatch")
    return _ok()


def validate_transfer_tx(
    tx: Transaction,
    block_height: int,
    params: ConsensusParams,
    utxo_view: UTXOReader,
    spent_in_block: set[Outpoint],
) -> Result:
    if classify_tx(tx) != TxClass.TRANSFER:
        return _ok()
    try:
        transfer = decode_transfer_data(tx.data)
    except ValueError:
        return _err("invalid transfer encoding")
    input_total = 0
    for inp in transfer.inputs:
        outpoint = Outpoint(txid=inp.prev_txid, index=inp.output_index)
        if outpoint in spent_in_block:
            return _err("double spend within block")
        if not utxo_view.is_unspent(outpoint):
            return _err("transfer spends missing or spent UTXO")
        utxo = utxo_view.get(outpoint)
        if utxo is None:
            return _err("transfer spends missing or spent UTXO")
        if utxo.recipient_pubkey != tx.sender_key:
            return _err("transfer input owner mismatch")
        if utxo.is_coinbase and utxo.height_created > 0:
            if block_height < utxo.height_created + params.coinbase_maturity:
                return _err("immature coinbase spend")
        input_total += utxo.amount
        spent_in_block.add(outpoint)
    output_total = sum(output.amount for output in transfer.outputs)
    if input_total < output_total:
        return _err("transfer outputs exceed inputs")
    return _ok()


def validate_block_transfers(
    block: Block,
    height: int,
    params: ConsensusParams,
    utxo_view: UTXOReader,
) -> Result:
    spent_in_block: set[Outpoint] = set()
    for tx in block.transactions[1:]:
        if classify_tx(tx) == TxClass.DATA_CARRIER:
            continue
        result = validate_transfer_tx(tx, height, params, utxo_view, spent_in_block)
        if not result.ok:
            return result
    return _ok()


def validate_block_stateful(
    block: Block,
    height: int,
    params: ConsensusParams,
    utxo_view: UTXOReader,
    *,
    parent_header: BlockHeader | None,
    parent_block_hash: bytes | None,
    now: int,
) -> Result:
    genesis = height == 0
    if not genesis:
        if parent_block_hash is None or parent_header is None:
            return _err("missing parent block")
        link_result = validate_prev_hash_link(block.header, parent_block_hash)
        if not link_result.ok:
            return link_result
        timestamp_result = validate_block_timestamp(
            block.header,
            parent_timestamp=parent_header.timestamp,
            now=now,
            tolerance_seconds=params.timestamp_tolerance_seconds,
            genesis=False,
        )
        if not timestamp_result.ok:
            return timestamp_result

    coinbase_result = validate_coinbase_value(block, height, params, utxo_view)
    if not coinbase_result.ok:
        return coinbase_result
    return validate_block_transfers(block, height, params, utxo_view)


def validate_block_stateless(
    block: Block,
    height: int,
    params: ConsensusParams,
    verify: VerifySignature,
    *,
    max_block_bytes: int = MAX_BLOCK_BYTES,
) -> Result:
    checks = (
        validate_header_size(block.header),
        validate_header_difficulty(block.header, params),
        validate_pow(block.header, genesis=height == 0),
        validate_txs_hash(block),
        validate_block_coinbase_rules(block, height),
        validate_block_size(block, max_block_bytes),
    )
    for result in checks:
        if not result.ok:
            return result

    for index, tx in enumerate(block.transactions):
        if _genesis_signature_exempt(tx, index, height, params):
            continue
        sig_result = validate_transaction_signature(tx, verify)
        if not sig_result.ok:
            return sig_result

    return _ok()
