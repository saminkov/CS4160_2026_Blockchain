from __future__ import annotations

import hashlib

import pytest

from blockchain.core.codec import pack_header
from blockchain.core.entities import HASH_SIZE, BlockHeader, Transaction
from blockchain.core.hashing import (
    block_hash,
    header_mining_prefix,
    tx_hash,
    txs_hash,
    txs_hash_from_digests,
)

_EMPTY_SHA256 = hashlib.sha256(b"").digest()
_ZERO_HASH = b"\x00" * HASH_SIZE
_PUBKEY = b"LibNaCLPK:test"


def _tx(
    *,
    sender_key: bytes = _PUBKEY,
    data: bytes = b"payload",
    timestamp: int = 1_700_000_000,
    signature: bytes = b"sig",
) -> Transaction:
    return Transaction(
        sender_key=sender_key,
        data=data,
        timestamp=timestamp,
        signature=signature,
    )


def _header(**kwargs: object) -> BlockHeader:
    defaults = {
        "prev_hash": _ZERO_HASH,
        "txs_hash": _EMPTY_SHA256,
        "timestamp": 1_700_000_000,
        "difficulty": 18,
        "nonce": 0,
    }
    defaults.update(kwargs)
    # type: ignore[arg-type]
    return BlockHeader(**defaults)


class TestTxHash:
    def test_stable_for_same_transaction(self) -> None:
        tx = _tx()
        assert tx_hash(tx) == tx_hash(tx)
        assert len(tx_hash(tx)) == HASH_SIZE

    def test_changes_when_payload_changes(self) -> None:
        assert tx_hash(_tx(data=b"a")) != tx_hash(_tx(data=b"b"))


class TestTxsHash:
    def test_empty_body_is_sha256_of_empty_bytes(self) -> None:
        assert txs_hash(()) == _EMPTY_SHA256
        assert txs_hash(()) != _ZERO_HASH

    def test_empty_body_from_digests(self) -> None:
        assert txs_hash_from_digests(()) == _EMPTY_SHA256

    def test_order_matters(self) -> None:
        a, b = _tx(data=b"a"), _tx(data=b"b")
        assert txs_hash((a, b)) != txs_hash((b, a))

    def test_matches_concatenated_digests(self) -> None:
        txs = (_tx(data=b"a"), _tx(data=b"b"))
        expected = hashlib.sha256(tx_hash(txs[0]) + tx_hash(txs[1])).digest()
        assert txs_hash(txs) == expected
        assert txs_hash_from_digests([tx_hash(txs[0]), tx_hash(txs[1])]) == expected

    def test_rejects_bad_digest_length(self) -> None:
        with pytest.raises(ValueError, match="tx_hashes\\[0\\]"):
            txs_hash_from_digests([b"\x00" * 31])


class TestBlockHash:
    def test_is_sha256_of_packed_header(self) -> None:
        header = _header(nonce=99)
        assert block_hash(header) == hashlib.sha256(pack_header(header)).digest()

    def test_nonce_changes_hash(self) -> None:
        h0 = _header(nonce=0)
        h1 = _header(nonce=1)
        assert block_hash(h0) != block_hash(h1)


class TestHeaderMiningPrefix:
    def test_is_first_76_bytes_of_header(self) -> None:
        header = _header()
        assert header_mining_prefix(header) == pack_header(header)[:76]
        assert len(header_mining_prefix(header)) == 76


class TestTxsHashFromDigestsMCDC:
    """Loop condition: bad digest at index > 0 (index 0 already tested)."""

    def test_rejects_bad_digest_at_second_index(self) -> None:
        good = b"\x00" * HASH_SIZE
        with pytest.raises(ValueError, match="tx_hashes\\[1\\]"):
            txs_hash_from_digests([good, b"\x00" * 31])
