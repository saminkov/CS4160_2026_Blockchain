from __future__ import annotations

from blockchain.adapters.block_store import InMemoryBlockStore
from blockchain.core.entities import HASH_SIZE, Block, BlockHeader, BlockNode
from blockchain.core.hashing import block_hash
from blockchain.ports.stores import BlockStorePort

# mypy conformance.
_b: BlockStorePort = InMemoryBlockStore()

_ZERO = b"\x00" * HASH_SIZE


def _node(height: int, prev_hash: bytes, *, nonce: int = 0) -> BlockNode:
    header = BlockHeader(
        prev_hash=prev_hash,
        txs_hash=_ZERO,
        timestamp=1_700_000_000 + height,
        difficulty=12,
        nonce=nonce,
    )
    h = block_hash(header)
    block = Block(header=header, transactions=())
    return BlockNode(block=block, block_hash=h, height=height, parent_hash=prev_hash)


def _chain(n: int) -> list[BlockNode]:
    """genesis (height 0) .. height n-1, each linking to the previous."""
    nodes = [_node(0, _ZERO)]
    for height in range(1, n):
        nodes.append(_node(height, nodes[-1].block_hash))
    return nodes


class TestAddGetHas:
    def test_add_then_get_and_has(self) -> None:
        store = InMemoryBlockStore()
        node = _node(0, _ZERO)
        store.add(node)
        assert store.get(node.block_hash) == node
        assert store.has(node.block_hash) is True

    def test_unknown_get_is_none_and_has_false(self) -> None:
        store = InMemoryBlockStore()
        assert store.get(_ZERO) is None
        assert store.has(_ZERO) is False
        assert store.node_at(0) is None


class TestMainChainAndTip:
    def test_tip_none_when_empty(self) -> None:
        assert InMemoryBlockStore().tip() is None

    def test_set_main_tracks_tip_and_node_at(self) -> None:
        store = InMemoryBlockStore()
        nodes = _chain(3)
        for node in nodes:
            store.add(node)
            store.set_main(node.height, node.block_hash)
        assert store.tip() == nodes[2]
        assert store.node_at(1) == nodes[1]


class TestAncestors:
    def test_inclusive_node_first_to_genesis(self) -> None:
        store = InMemoryBlockStore()
        g, h1, h2 = _chain(3)
        for node in (g, h1, h2):
            store.add(node)
        assert store.ancestors(h2.block_hash) == [h2, h1, g]

    def test_max_depth_caps_result(self) -> None:
        store = InMemoryBlockStore()
        g, h1, h2 = _chain(3)
        for node in (g, h1, h2):
            store.add(node)
        assert store.ancestors(h2.block_hash, max_depth=2) == [h2, h1]

    def test_genesis_alone(self) -> None:
        store = InMemoryBlockStore()
        g = _node(0, _ZERO)
        store.add(g)
        assert store.ancestors(g.block_hash) == [g]


class TestOrphans:
    def test_add_and_take_multiple_children(self) -> None:
        store = InMemoryBlockStore()
        parent = _node(5, _ZERO)
        child_a = _node(6, parent.block_hash, nonce=1)
        child_b = _node(6, parent.block_hash, nonce=2)
        store.add_orphan(child_a)
        store.add_orphan(child_b)
        taken = store.take_orphans(parent.block_hash)
        assert set(taken) == {child_a, child_b}
        # taking again yields nothing (they were removed)
        assert store.take_orphans(parent.block_hash) == []

    def test_take_unknown_parent_is_empty(self) -> None:
        assert InMemoryBlockStore().take_orphans(_ZERO) == []
