from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from blockchain.core.entities import (
    UTXO,
    Block,
    BlockNode,
    Outpoint,
    Transaction,
    UndoRecord,
)


class BlockStorePort(Protocol):
    """Block-tree index plus main-chain map and orphan buffer"""

    def add(self, node: BlockNode) -> None:
        """Insert a block node keyed by its hash."""
        ...

    def get(self, block_hash: bytes) -> BlockNode | None:
        """Return the node with this hash, or None if unknown."""
        ...

    def has(self, block_hash: bytes) -> bool:
        """Return True iff a node with this hash is stored."""
        ...

    def node_at(self, height: int) -> BlockNode | None:
        """Return the main-chain node at ``height``, or None."""
        ...

    def tip(self) -> BlockNode | None:
        """Return the current main-chain tip, or None if empty."""
        ...

    def set_main(self, height: int, block_hash: bytes) -> None:
        """Record ``block_hash`` as the main-chain block at ``height``."""
        ...

    def remove_main_from(self, height: int) -> None:
        """Drop main-chain entries at ``height`` and above."""
        ...

    def ancestors(self, block_hash: bytes, max_depth: int | None = None) -> list[BlockNode]:
        """Return nodes from ``block_hash`` toward genesis, inclusive and node-first.

        The queried node is first, then its parent, and so on until genesis (the walk
        stops when a ``parent_hash`` is unknown). ``max_depth`` caps the number of nodes
        returned; ``None`` walks all the way to genesis.
        """
        ...

    def add_orphan(self, node: BlockNode) -> None:
        """Buffer a node whose parent is not yet known."""
        ...

    def take_orphans(self, parent_hash: bytes) -> list[BlockNode]:
        """Remove and return orphans whose parent is ``parent_hash``."""
        ...

    def missing_orphan_parents(self) -> list[bytes]:
        """Return parent hashes of buffered orphans that are not yet stored.

        These are the still-missing links whose retrieval would let buffered branches
        connect; used by the sync re-scan to re-ask peers until a branch links.
        """
        ...


class UTXOView(Protocol):
    """Read-only snapshot of the UTXO set, safe to offload for validation."""

    def get(self, outpoint: Outpoint) -> UTXO | None:
        """Return the UTXO at ``outpoint``, or None if absent/spent."""
        ...

    def is_unspent(self, outpoint: Outpoint) -> bool:
        """Return True iff ``outpoint`` is a currently unspent output."""
        ...


class UTXOStorePort(UTXOView, Protocol):
    """Mutable UTXO set with per-block undo support for reorgs"""

    def apply(self, block: Block, height: int) -> UndoRecord:
        """Apply a block's effects and return the record needed to undo them."""
        ...

    def rollback(self, undo: UndoRecord) -> None:
        """Invert a previously applied block using its undo record."""
        ...

    def read_view(self) -> UTXOView:
        """Return a read-only snapshot for offloaded (stateless) validation."""
        ...


class MempoolPort(Protocol):
    """Hash-indexed, fee-ordered pending-transaction pool."""

    def add(self, tx: Transaction, fee: int) -> None:
        """Add a transaction with its computed fee."""
        ...

    def remove(self, tx_hash: bytes) -> None:
        """Remove the transaction with this hash, if present."""
        ...

    def contains(self, tx_hash: bytes) -> bool:
        """Return True iff a transaction with this hash is pooled."""
        ...

    def select(self, size_limit: int) -> list[Transaction]:
        """Return fee-ordered transactions fitting within ``size_limit`` bytes."""
        ...

    def evict_if_full(self) -> None:
        """Drop the lowest-fee entry when the pool exceeds its capacity."""
        ...

    def restore(self, txs: Iterable[Transaction]) -> None:
        """Re-add transactions orphaned by a reorg."""
        ...

    def snapshot(self) -> tuple[Transaction, ...]:
        """Return all pooled transactions in arbitrary order."""
        ...

    def __len__(self) -> int:
        """Return the number of pooled transactions."""
        ...
