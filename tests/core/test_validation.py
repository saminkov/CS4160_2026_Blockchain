from __future__ import annotations

import pytest

from blockchain.core.codec import (
    CoinbaseData,
    CoinbaseOutput,
    TransferData,
    TransferInput,
    TransferOutput,
    encode_coinbase_data,
    encode_transfer_data,
)
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import HASH_SIZE, Block, BlockHeader, Transaction
from blockchain.core.hashing import header_mining_prefix, txs_hash
from blockchain.core.pow import search_nonce
from blockchain.core.validation import (
    MAX_BLOCK_BYTES,
    TxClass,
    classify_tx,
    validate_block_coinbase_rules,
    validate_block_size,
    validate_block_stateless,
    validate_coinbase_structure,
    validate_txs_hash,
)

_ZERO_HASH = b"\x00" * HASH_SIZE
_PUBKEY_A = b"LibNaCLPK:alice"
_PUBKEY_B = b"LibNaCLPK:bob"


def _always_valid(_pubkey: bytes, _message: bytes, _signature: bytes) -> bool:
    return True


def _always_invalid(_pubkey: bytes, _message: bytes, _signature: bytes) -> bool:
    return False


def _header(*, txs_hash_value: bytes = _ZERO_HASH, difficulty: int = 12, nonce: int = 42) -> BlockHeader:
    return BlockHeader(
        prev_hash=_ZERO_HASH,
        txs_hash=txs_hash_value,
        timestamp=1_700_000_000,
        difficulty=difficulty,
        nonce=nonce,
    )


def _tx(*, data: bytes = b"opaque-payload") -> Transaction:
    return Transaction(
        sender_key=_PUBKEY_A,
        data=data,
        timestamp=1_700_000_000,
        signature=b"sig-bytes",
    )


def _coinbase_tx(height: int) -> Transaction:
    data = encode_coinbase_data(
        CoinbaseData(
            height=height,
            outputs=(CoinbaseOutput(recipient_pubkey=_PUBKEY_A, amount=100),),
        )
    )
    return _tx(data=data)


def _transfer_tx() -> Transaction:
    data = encode_transfer_data(
        TransferData(
            inputs=(TransferInput(prev_txid=_ZERO_HASH, output_index=0),),
            outputs=(TransferOutput(recipient_pubkey=_PUBKEY_B, amount=50),),
        )
    )
    return _tx(data=data)


def _block(*transactions: Transaction, header: BlockHeader | None = None) -> Block:
    txs = tuple(transactions)
    return Block(header=header or _header(txs_hash_value=txs_hash(txs)), transactions=txs)


@pytest.mark.parametrize(
    ("tx", "expected"),
    [
        (_coinbase_tx(1), TxClass.COINBASE),
        (_transfer_tx(), TxClass.TRANSFER),
        (_tx(data=b"server-test-payload"), TxClass.DATA_CARRIER),
        (_tx(data=b"UTX1" + b"\x00\x01" + b"x" * 10), TxClass.DATA_CARRIER),
    ],
)
def test_classify_tx(tx: Transaction, expected: TxClass) -> None:
    assert classify_tx(tx) == expected


def test_txs_hash_mismatch_fails() -> None:
    coinbase = _coinbase_tx(1)
    block = _block(coinbase, header=_header(txs_hash_value=_ZERO_HASH))
    assert not validate_txs_hash(block).ok


def test_coinbase_must_be_first_with_matching_height() -> None:
    assert not validate_block_coinbase_rules(_block(_transfer_tx()), 1).ok
    assert not validate_coinbase_structure(_coinbase_tx(2), 1).ok
    assert validate_coinbase_structure(_coinbase_tx(1), 1).ok


def test_only_one_coinbase_allowed() -> None:
    block = _block(_coinbase_tx(1), _coinbase_tx(1))
    assert not validate_block_coinbase_rules(block, 1).ok


def test_block_exceeds_size_cap() -> None:
    txs = tuple(_tx(data=b"x" * 50_000) for _ in range(25))
    assert not validate_block_size(_block(*txs), max_bytes=MAX_BLOCK_BYTES).ok


def test_genesis_stateless_passes_without_pow_or_real_signature() -> None:
    params = ConsensusParams.default()
    genesis = params.build_genesis()
    result = validate_block_stateless(
        genesis.block,
        height=0,
        params=params,
        verify=_always_invalid,
    )
    assert result.ok


def test_non_genesis_stateless_rejects_invalid_signatures() -> None:
    params = ConsensusParams.default()
    block = _block(_coinbase_tx(1))
    result = validate_block_stateless(block, height=1, params=params, verify=_always_invalid)
    assert not result.ok


def test_non_genesis_stateless_passes_with_pow_and_signatures() -> None:
    params = ConsensusParams.default()
    coinbase = _coinbase_tx(1)
    draft = _header(txs_hash_value=txs_hash((coinbase,)), difficulty=params.difficulty_bits)
    nonce = search_nonce(
        header_mining_prefix(draft),
        params.difficulty_bits,
        should_abort=lambda: False,
    )
    assert nonce is not None
    header = _header(
        txs_hash_value=txs_hash((coinbase,)),
        difficulty=params.difficulty_bits,
        nonce=nonce,
    )
    block = Block(header=header, transactions=(coinbase,))
    result = validate_block_stateless(block, height=1, params=params, verify=_always_valid)
    assert result.ok
