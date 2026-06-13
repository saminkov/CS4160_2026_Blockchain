from __future__ import annotations

from blockchain.core.entities import BlockNode


def better_tip(a: BlockNode, b: BlockNode) -> BlockNode:
    if a.height != b.height:
        return a if a.height > b.height else b
    if a.block_hash <= b.block_hash:
        return a
    return b
