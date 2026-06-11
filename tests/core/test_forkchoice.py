from __future__ import annotations

import pytest

from blockchain.core.entities import HASH_SIZE, Block, BlockHeader, BlockNode
from blockchain.core.forkchoice import better_tip

_ZERO_HASH = b"\x00" * HASH_SIZE
_HEADER = BlockHeader(
    prev_hash=_ZERO_HASH,
    txs_hash=_ZERO_HASH,
    timestamp=1_700_000_000,
    difficulty=12,
    nonce=0,
)
_BLOCK = Block(header=_HEADER, transactions=())


def _node(height: int, block_hash: bytes) -> BlockNode:
    return BlockNode(
        block=_BLOCK,
        block_hash=block_hash,
        height=height,
        parent_hash=_ZERO_HASH,
    )


def test_taller_chain_wins() -> None:
    low = _node(3, b"\xff" * HASH_SIZE)
    high = _node(5, b"\x00" * HASH_SIZE)
    assert better_tip(low, high) is high
    assert better_tip(high, low) is high


@pytest.mark.parametrize(
    ("hash_a", "hash_b", "expected_is_a"),
    [
        (b"\x00" + b"\x01" * 31, b"\x01" + b"\x00" * 31, True),
        (b"\x01" + b"\x00" * 31, b"\x00" + b"\x01" * 31, False),
    ],
)
def test_equal_height_prefers_lower_block_hash(
    hash_a: bytes,
    hash_b: bytes,
    expected_is_a: bool,
) -> None:
    a = _node(4, hash_a)
    b = _node(4, hash_b)
    winner = better_tip(a, b)
    assert winner is (a if expected_is_a else b)
    assert better_tip(b, a) is winner


def test_same_tip_is_stable() -> None:
    tip = _node(2, b"\xab" * HASH_SIZE)
    assert better_tip(tip, tip) is tip
