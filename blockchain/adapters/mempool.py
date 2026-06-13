from __future__ import annotations

import heapq
from collections.abc import Iterable

from blockchain.core.codec import pack_tx
from blockchain.core.entities import Transaction
from blockchain.core.hashing import tx_hash
from blockchain.ports.stores import MempoolPort

DEFAULT_MEMPOOL_CAPACITY = 20_000


class InMemoryMempool(MempoolPort):
    """Fee-indexed, arrival-ordered pending-transaction pool with lazy-eviction."""

    def __init__(self, capacity: int = DEFAULT_MEMPOOL_CAPACITY) -> None:
        self._capacity = capacity
        self._by_hash: dict[bytes, Transaction] = {}
        self._fees: dict[bytes, int] = {}
        self._counter = 0
        self._seq: dict[bytes, int] = {}
        self._evict_heap: list[tuple[int, int, bytes]] = []

    def add(self, tx: Transaction, fee: int) -> None:
        h = tx_hash(tx)
        if h in self._by_hash:
            return
        self._counter += 1
        self._by_hash[h] = tx
        self._fees[h] = fee
        self._seq[h] = self._counter
        heapq.heappush(self._evict_heap, (fee, self._counter, h))
        self.evict_if_full()

    def remove(self, tx_hash: bytes) -> None:
        self._by_hash.pop(tx_hash, None)
        self._fees.pop(tx_hash, None)
        self._seq.pop(tx_hash, None)

    def contains(self, tx_hash: bytes) -> bool:
        return tx_hash in self._by_hash

    def select(self, size_limit: int) -> list[Transaction]:
        sorted_hashes = sorted(
            self._by_hash.keys(),
            key=lambda h: (-self._fees[h], self._by_hash[h].timestamp, h),
        )
        selected: list[Transaction] = []
        cumulative_size = 0
        for h in sorted_hashes:
            if h not in self._by_hash:
                continue
            tx = self._by_hash[h]
            tx_size = len(pack_tx(tx))
            if cumulative_size + tx_size <= size_limit:
                selected.append(tx)
                cumulative_size += tx_size
        return selected

    def evict_if_full(self) -> None:
        while len(self._by_hash) > self._capacity:
            while self._evict_heap:
                fee, seq, h = heapq.heappop(self._evict_heap)
                if h in self._by_hash:
                    self.remove(h)
                    break

    def restore(self, txs: Iterable[Transaction]) -> None:
        for tx in txs:
            if tx_hash(tx) not in self._by_hash:
                self.add(tx, 0)

    def snapshot(self) -> tuple[Transaction, ...]:
        return tuple(self._by_hash.values())

    def __len__(self) -> int:
        return len(self._by_hash)
