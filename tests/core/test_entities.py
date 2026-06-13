from __future__ import annotations

import pytest

from blockchain.core.entities import (
    HASH_SIZE,
    HEADER_SIZE,
    UTXO,
    Block,
    BlockHeader,
    BlockNode,
    Outpoint,
    Result,
    Transaction,
    UndoRecord,
)

_ZERO_HASH = b"\x00" * HASH_SIZE
_PUBKEY = b"LibNaCLPK:test"


def _header(
    *,
    prev_hash: bytes = _ZERO_HASH,
    txs_hash: bytes = _ZERO_HASH,
    timestamp: int = 1_700_000_000,
    difficulty: int = 18,
    nonce: int = 42,
) -> BlockHeader:
    return BlockHeader(
        prev_hash=prev_hash,
        txs_hash=txs_hash,
        timestamp=timestamp,
        difficulty=difficulty,
        nonce=nonce,
    )


def _tx(
    *,
    sender_key: bytes = _PUBKEY,
    data: bytes = b"hello",
    timestamp: int = 1_700_000_000,
    signature: bytes = b"sig",
) -> Transaction:
    return Transaction(
        sender_key=sender_key,
        data=data,
        timestamp=timestamp,
        signature=signature,
    )


class TestConstants:
    def test_header_size(self) -> None:
        assert HEADER_SIZE == 84


class TestBlockHeader:
    def test_valid_header(self) -> None:
        h = _header()
        assert h.nonce == 42
        assert h.difficulty == 18

    def test_rejects_short_prev_hash(self) -> None:
        with pytest.raises(ValueError, match="prev_hash"):
            _header(prev_hash=b"\x00" * 31)

    def test_rejects_negative_timestamp(self) -> None:
        with pytest.raises(ValueError, match="timestamp"):
            _header(timestamp=-1)

    def test_rejects_difficulty_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="difficulty"):
            _header(difficulty=0x1_0000_0000)


class TestTransaction:
    def test_valid_transaction(self) -> None:
        tx = _tx()
        assert tx.data == b"hello"

    def test_rejects_empty_sender_key(self) -> None:
        with pytest.raises(ValueError, match="sender_key"):
            _tx(sender_key=b"")

    def test_frozen(self) -> None:
        tx = _tx()
        with pytest.raises(AttributeError):
            # type: ignore[misc]
            tx.timestamp = 0


class TestBlock:
    def test_empty_transaction_list(self) -> None:
        block = Block(header=_header(), transactions=())
        assert block.transactions == ()


class TestOutpoint:
    def test_hashable_for_utxo_map_keys(self) -> None:
        op = Outpoint(txid=_ZERO_HASH, index=0)
        assert {op: "value"}[op] == "value"

    def test_rejects_index_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="index"):
            Outpoint(txid=_ZERO_HASH, index=0x1_0000)


class TestUTXO:
    def test_valid_utxo(self) -> None:
        op = Outpoint(txid=_ZERO_HASH, index=1)
        utxo = UTXO(
            outpoint=op,
            recipient_pubkey=_PUBKEY,
            amount=100,
            height_created=0,
            is_coinbase=True,
        )
        assert utxo.is_coinbase is True

    def test_rejects_empty_recipient(self) -> None:
        op = Outpoint(txid=_ZERO_HASH, index=0)
        with pytest.raises(ValueError, match="recipient_pubkey"):
            UTXO(
                outpoint=op,
                recipient_pubkey=b"",
                amount=1,
                height_created=0,
                is_coinbase=False,
            )


class TestBlockNode:
    def test_genesis_node(self) -> None:
        genesis_block = Block(header=_header(), transactions=(_tx(),))
        node = BlockNode(
            block=genesis_block,
            block_hash=_ZERO_HASH,
            height=0,
            parent_hash=_ZERO_HASH,
        )
        assert node.height == 0


class TestUndoRecord:
    def test_empty_undo(self) -> None:
        undo = UndoRecord(spent=(), created=())
        assert undo.spent == ()
        assert undo.created == ()

    def test_round_trip_fields(self) -> None:
        op = Outpoint(txid=_ZERO_HASH, index=0)
        utxo = UTXO(
            outpoint=op,
            recipient_pubkey=_PUBKEY,
            amount=50,
            height_created=1,
            is_coinbase=False,
        )
        undo = UndoRecord(spent=(utxo,), created=(op,))
        assert undo.spent[0].amount == 50
        assert undo.created[0].index == 0


class TestResult:
    def test_success(self) -> None:
        result = Result(ok=True, reason="")
        assert result.ok is True
        assert result.reason == ""

    def test_failure(self) -> None:
        result = Result(ok=False, reason="invalid signature")
        assert result.ok is False
        assert result.reason == "invalid signature"

    def test_equality(self) -> None:
        assert Result(ok=True, reason="") == Result(ok=True, reason="")
        assert Result(ok=False, reason="x") != Result(ok=False, reason="y")

class TestBlockHeaderMCDC:
    """_check_uint64 (nonce, timestamp) and _check_uint32 (difficulty) overflow/negative paths."""

    def test_rejects_short_txs_hash(self) -> None:
        with pytest.raises(ValueError, match="txs_hash"):
            _header(txs_hash=b"\x00" * 31)

    def test_rejects_negative_difficulty(self) -> None:
        with pytest.raises(ValueError, match="difficulty"):
            _header(difficulty=-1)

    def test_rejects_timestamp_overflow(self) -> None:
        with pytest.raises(ValueError, match="timestamp"):
            _header(timestamp=0x1_0000_0000_0000_0000)

    def test_rejects_nonce_overflow(self) -> None:
        with pytest.raises(ValueError, match="nonce"):
            _header(nonce=0x1_0000_0000_0000_0000)


class TestOutpointMCDC:
    """_check_uint16: negative index (left side of OR not yet tested)."""

    def test_rejects_negative_index(self) -> None:
        with pytest.raises(ValueError, match="index"):
            Outpoint(txid=_ZERO_HASH, index=-1)


class TestUTXOMCDC:
    """UTXO.__post_init__ branches not previously exercised."""

    def test_rejects_negative_height_created(self) -> None:
        op = Outpoint(txid=_ZERO_HASH, index=0)
        with pytest.raises(ValueError, match="height_created"):
            UTXO(outpoint=op, recipient_pubkey=_PUBKEY, amount=1, height_created=-1, is_coinbase=False)

    def test_rejects_amount_overflow(self) -> None:
        op = Outpoint(txid=_ZERO_HASH, index=0)
        with pytest.raises(ValueError, match="amount"):
            UTXO(
                outpoint=op,
                recipient_pubkey=_PUBKEY,
                amount=0x1_0000_0000_0000_0000,
                height_created=0,
                is_coinbase=False,
            )


class TestBlockNodeMCDC:
    """BlockNode.height < 0 branch never reached by existing tests (only height=0)."""

    def test_rejects_negative_height(self) -> None:
        block = Block(header=_header(), transactions=())
        with pytest.raises(ValueError, match="height"):
            BlockNode(block=block, block_hash=_ZERO_HASH, height=-1, parent_hash=_ZERO_HASH)
