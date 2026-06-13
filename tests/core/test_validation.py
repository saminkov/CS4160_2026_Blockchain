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
    validate_header_difficulty,
    validate_pow,
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


class TestValidatePowMCDC:
    """validate_pow: `if genesis` True/False and the leading-zero fail branch."""

    def test_genesis_always_passes(self) -> None:
        header = _header(difficulty=32, nonce=0)  # unsatisfiable PoW
        assert validate_pow(header, genesis=True).ok

    def test_non_genesis_fails_unsatisfied_pow(self) -> None:
        header = _header(difficulty=32, nonce=0)
        assert not validate_pow(header, genesis=False).ok

    def test_non_genesis_passes_satisfied_pow(self) -> None:
        params = ConsensusParams.default()
        coinbase = _coinbase_tx(1)
        draft = _header(txs_hash_value=txs_hash((coinbase,)), difficulty=params.difficulty_bits)
        nonce = search_nonce(
            header_mining_prefix(draft), params.difficulty_bits, should_abort=lambda: False
        )
        assert nonce is not None
        header = _header(
            txs_hash_value=txs_hash((coinbase,)), difficulty=params.difficulty_bits, nonce=nonce
        )
        assert validate_pow(header, genesis=False).ok


class TestValidateHeaderDifficultyMCDC:
    def test_matching_difficulty_passes(self) -> None:
        params = ConsensusParams.default()
        assert validate_header_difficulty(_header(difficulty=params.difficulty_bits), params).ok

    def test_mismatched_difficulty_fails(self) -> None:
        params = ConsensusParams.default()
        assert not validate_header_difficulty(_header(difficulty=params.difficulty_bits + 1), params).ok


class TestGenesisSignatureExemptMCDC:
    """
    _genesis_signature_exempt has 3 conditions joined with `and`.
    Existing tests cover T T T (genesis passes) and F * * (height=1 rejects).
    Missing: T F * (tx_index != 0) and T T F (wrong signature).
    """

    def test_tx_index_nonzero_not_exempt(self) -> None:
        """height=0, tx_index=1 — second condition independently causes False."""
        params = ConsensusParams.default()
        genesis = params.build_genesis()
        coinbase0 = genesis.block.transactions[0]
        # Second tx is a data-carrier (not coinbase) carrying the genesis signature
        extra = Transaction(
            sender_key=coinbase0.sender_key,
            data=b"not-coinbase-data",
            timestamp=coinbase0.timestamp,
            signature=params.genesis_coinbase_signature,
        )
        txs = (coinbase0, extra)
        header = BlockHeader(
            prev_hash=genesis.block.header.prev_hash,
            txs_hash=txs_hash(txs),
            timestamp=genesis.block.header.timestamp,
            difficulty=genesis.block.header.difficulty,
            nonce=genesis.block.header.nonce,
        )
        block = Block(header=header, transactions=txs)
        # coinbase0 at idx=0 is exempt; extra at idx=1 is not → _always_invalid rejects it
        result = validate_block_stateless(block, height=0, params=params, verify=_always_invalid)
        assert not result.ok

    def test_wrong_signature_not_exempt(self) -> None:
        """height=0, tx_index=0, wrong sig — third condition independently causes False."""
        params = ConsensusParams.default()
        genesis = params.build_genesis()
        coinbase = genesis.block.transactions[0]
        bad_coinbase = Transaction(
            sender_key=coinbase.sender_key,
            data=coinbase.data,
            timestamp=coinbase.timestamp,
            signature=b"NOT_GENESIS",
        )
        header = BlockHeader(
            prev_hash=genesis.block.header.prev_hash,
            txs_hash=txs_hash((bad_coinbase,)),
            timestamp=genesis.block.header.timestamp,
            difficulty=genesis.block.header.difficulty,
            nonce=genesis.block.header.nonce,
        )
        block = Block(header=header, transactions=(bad_coinbase,))
        result = validate_block_stateless(block, height=0, params=params, verify=_always_invalid)
        assert not result.ok


class TestValidateCoinbaseStructureMCDC:
    def test_non_coinbase_tx_fails(self) -> None:
        assert not validate_coinbase_structure(_transfer_tx(), block_height=1).ok

    def test_malformed_coinbase_data_fails(self) -> None:
        bad = Transaction(
            sender_key=_PUBKEY_A,
            data=b"CBAS\x00\x00\x00",
            timestamp=1_700_000_000,
            signature=b"sig",
        )
        assert not validate_coinbase_structure(bad, block_height=1).ok

    def test_valid_coinbase_passes(self) -> None:
        assert validate_coinbase_structure(_coinbase_tx(1), block_height=1).ok


class TestValidateBlockCoinbaseRulesMCDC:
    def test_empty_block_fails(self) -> None:
        empty = Block(header=_header(), transactions=())
        assert not validate_block_coinbase_rules(empty, block_height=1).ok


class TestValidateTxsHashMCDC:
    def test_matching_hash_passes(self) -> None:
        coinbase = _coinbase_tx(1)
        assert validate_txs_hash(_block(coinbase)).ok


class TestValidateBlockSizeMCDC:
    def test_small_block_passes(self) -> None:
        assert validate_block_size(_block(_coinbase_tx(1))).ok
