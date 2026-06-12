from __future__ import annotations

from blockchain.adapters.mempool import DEFAULT_MEMPOOL_CAPACITY, InMemoryMempool
from blockchain.core.entities import Transaction
from blockchain.core.hashing import tx_hash
from blockchain.ports.stores import MempoolPort

# mypy conformance.
_m: MempoolPort = InMemoryMempool()

_PK = b"LibNaCLPK:alice"


def _tx(payload: bytes, *, timestamp: int = 1_700_000_000) -> Transaction:
    return Transaction(sender_key=_PK, data=payload, timestamp=timestamp, signature=b"sig")


class TestAddDedupLen:
    def test_add_contains_len(self) -> None:
        pool = InMemoryMempool()
        tx = _tx(b"a")
        pool.add(tx, fee=1)
        assert pool.contains(tx_hash(tx)) is True
        assert len(pool) == 1

    def test_duplicate_add_is_idempotent(self) -> None:
        pool = InMemoryMempool()
        tx = _tx(b"a")
        pool.add(tx, fee=1)
        pool.add(tx, fee=5)
        assert len(pool) == 1


class TestRemove:
    def test_remove_deletes(self) -> None:
        pool = InMemoryMempool()
        tx = _tx(b"a")
        pool.add(tx, fee=1)
        pool.remove(tx_hash(tx))
        assert pool.contains(tx_hash(tx)) is False
        assert len(pool) == 0

    def test_remove_unknown_is_noop(self) -> None:
        pool = InMemoryMempool()
        pool.remove(b"\x00" * 32)
        assert len(pool) == 0


class TestSelect:
    def test_orders_by_fee_descending(self) -> None:
        pool = InMemoryMempool()
        low, high = _tx(b"low"), _tx(b"high")
        pool.add(low, fee=1)
        pool.add(high, fee=10)
        assert pool.select(size_limit=10_000) == [high, low]

    def test_tie_break_timestamp_then_hash(self) -> None:
        pool = InMemoryMempool()
        older = _tx(b"x", timestamp=100)
        newer = _tx(b"y", timestamp=200)
        pool.add(newer, fee=5)
        pool.add(older, fee=5)
        # equal fee → older timestamp first
        assert pool.select(size_limit=10_000)[0] == older

    def test_respects_size_limit(self) -> None:
        pool = InMemoryMempool()
        a, b = _tx(b"a"), _tx(b"b")
        pool.add(a, fee=10)
        pool.add(b, fee=1)
        from blockchain.core.codec import pack_tx

        only_one = pack_tx(a)
        selected = pool.select(size_limit=len(only_one))
        assert selected == [a]


class TestEviction:
    def test_evicts_lowest_fee_at_capacity(self) -> None:
        pool = InMemoryMempool(capacity=2)
        a, b, c = _tx(b"a"), _tx(b"b"), _tx(b"c")
        pool.add(a, fee=5)
        pool.add(b, fee=1)  # lowest fee
        pool.add(c, fee=9)
        assert len(pool) == 2
        assert pool.contains(tx_hash(b)) is False

    def test_eviction_tie_break_oldest_arrival(self) -> None:
        pool = InMemoryMempool(capacity=2)
        first, second, third = _tx(b"1"), _tx(b"2"), _tx(b"3")
        pool.add(first, fee=1)  # equal fee, arrived first → evicted
        pool.add(second, fee=1)
        pool.add(third, fee=9)
        assert len(pool) == 2
        assert pool.contains(tx_hash(first)) is False


class TestRestore:
    def test_restore_readds_deduped(self) -> None:
        pool = InMemoryMempool()
        a, b = _tx(b"a"), _tx(b"b")
        pool.add(a, fee=5)
        pool.restore([a, b])  # a already present, b new
        assert len(pool) == 2
        assert pool.contains(tx_hash(b)) is True


def test_default_capacity_is_20k() -> None:
    assert DEFAULT_MEMPOOL_CAPACITY == 20_000
