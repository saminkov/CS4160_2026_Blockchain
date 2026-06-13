from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

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
    Result,
    Transaction,
)
from blockchain.core.hashing import block_hash, txs_hash
from blockchain.core.pow import has_leading_zero_bits

MAX_BLOCK_BYTES = 1_048_576

VerifySignature = Callable[[bytes, bytes, bytes], bool]


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
