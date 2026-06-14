from __future__ import annotations

from blockchain.core.entities import BlockNode
from blockchain.ports.stores import BlockStorePort


class InMemoryBlockStore(BlockStorePort):
    """Block-tree index keyed by hash, with a main-chain map and orphan buffer."""

    def __init__(self) -> None:
        self._nodes: dict[bytes, BlockNode] = {}
        self._main: dict[int, bytes] = {}
        self._tip: BlockNode | None = None
        self._orphans: dict[bytes, list[BlockNode]] = {}

    def add(self, node: BlockNode) -> None:
        self._nodes[node.block_hash] = node

    def get(self, block_hash: bytes) -> BlockNode | None:
        return self._nodes.get(block_hash)

    def has(self, block_hash: bytes) -> bool:
        return block_hash in self._nodes

    def node_at(self, height: int) -> BlockNode | None:
        block_hash = self._main.get(height)
        return self._nodes.get(block_hash) if block_hash is not None else None

    def tip(self) -> BlockNode | None:
        return self._tip

    def set_main(self, height: int, block_hash: bytes) -> None:
        self._main[height] = block_hash
        if self._tip is None or height >= self._tip.height:
            self._tip = self._nodes.get(block_hash)

    def remove_main_from(self, height: int) -> None:
        for main_height in list(self._main):
            if main_height >= height:
                del self._main[main_height]
        self._refresh_tip()

    def _refresh_tip(self) -> None:
        if not self._main:
            self._tip = None
            return
        tip_height = max(self._main)
        self._tip = self._nodes.get(self._main[tip_height])

    def ancestors(self, block_hash: bytes, max_depth: int | None = None) -> list[BlockNode]:
        chain: list[BlockNode] = []
        current = self._nodes.get(block_hash)
        while current is not None:
            chain.append(current)
            if max_depth is not None and len(chain) >= max_depth:
                break
            current = self._nodes.get(current.parent_hash)
        return chain

    def add_orphan(self, node: BlockNode) -> None:
        self._orphans.setdefault(node.parent_hash, []).append(node)

    def take_orphans(self, parent_hash: bytes) -> list[BlockNode]:
        return self._orphans.pop(parent_hash, [])
