from __future__ import annotations

import dataclasses
from typing import Any

from blockchain.adapters.block_store import InMemoryBlockStore
from blockchain.adapters.mempool import InMemoryMempool
from blockchain.adapters.payloads import BlockInvPayload
from blockchain.adapters.system_clock import FakeClock
from blockchain.adapters.utxo_store import InMemoryUTXOStore
from blockchain.app.chain_service import ChainService
from blockchain.app.sync_service import SyncService
from blockchain.app.validation_service import ValidationService
from blockchain.core.codec import CoinbaseData, CoinbaseOutput, encode_coinbase_data
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import Block, BlockHeader, Transaction
from blockchain.core.hashing import block_hash, header_mining_prefix, txs_hash
from blockchain.core.pow import search_nonce

# Low difficulty keeps PoW search instant for the multi-block fork scenario.
_PARAMS = dataclasses.replace(ConsensusParams.default(), difficulty_bits=8)
_PK = b"LibNaCLPK:miner"


class _FakeCrypto:
    verify = staticmethod(lambda _pk, _msg, _sig: True)


def _coinbase(height: int, *, timestamp: int) -> Transaction:
    data = encode_coinbase_data(
        CoinbaseData(
            height=height,
            outputs=(CoinbaseOutput(recipient_pubkey=_PK, amount=_PARAMS.reward(height)),),
        )
    )
    return Transaction(sender_key=_PK, data=data, timestamp=timestamp, signature=b"sig")


def _mine(prev_hash: bytes, height: int, *, timestamp: int) -> Block:
    txs = (_coinbase(height, timestamp=timestamp),)
    base = BlockHeader(
        prev_hash=prev_hash,
        txs_hash=txs_hash(txs),
        timestamp=timestamp,
        difficulty=_PARAMS.difficulty_bits,
        nonce=0,
    )
    nonce = search_nonce(
        header_mining_prefix(base), _PARAMS.difficulty_bits, should_abort=lambda: False
    )
    assert nonce is not None
    return Block(header=dataclasses.replace(base, nonce=nonce), transactions=txs)


class _Node:
    """A ChainService + SyncService pair wired to a synchronous fake network."""

    def __init__(self, node_id: bytes) -> None:
        self.id = node_id
        utxo = InMemoryUTXOStore()
        self.chain = ChainService(
            _PARAMS,
            InMemoryBlockStore(),
            utxo,
            InMemoryMempool(),
            ValidationService(_PARAMS, None, utxo, _FakeCrypto()),
            FakeClock(_PARAMS.genesis_timestamp + 1_000_000),
        )
        self.chain.initialize_genesis()
        self.handlers: dict[type, Any] = {}
        self.tasks: dict[str, Any] = {}
        self.peer: _Node | None = None
        SyncService(self.chain, self, _PARAMS)  # type: ignore[arg-type]

    # --- NetworkPort surface used by SyncService ---
    def register_handler(self, payload_cls: type, handler: Any) -> None:
        self.handlers[payload_cls] = handler

    def register_task(self, name: str, fn: Any, interval: float) -> None:
        self.tasks[name] = fn

    def members_online(self) -> set[bytes]:
        return {self.peer.id} if self.peer is not None else set()

    def send(self, peer: bytes, payload: Any) -> None:
        target = self.peer
        if target is None:
            return
        handler = target.handlers.get(type(payload))
        if handler is not None:
            handler(self.id, payload)

    def broadcast_members(self, payload: Any) -> None:
        if self.peer is not None:
            self.send(self.peer.id, payload)


def _build_chain(prev: bytes, start_height: int, count: int, *, ts0: int) -> list[Block]:
    blocks: list[Block] = []
    parent = prev
    for i in range(count):
        block = _mine(parent, start_height + i, timestamp=ts0 + i)
        blocks.append(block)
        parent = block_hash(block.header)
    return blocks


def _feed(node: _Node, blocks: list[Block]) -> None:
    for block in blocks:
        node.chain.connect_block(block, source="local")


class TestSyncConvergence:
    def test_inv_triggers_ancestor_walk_and_reorg(self) -> None:
        """A shorter-fork node converges to the longer fork via gossip + ancestor pull."""
        genesis = _PARAMS.build_genesis().block_hash
        a, b = _Node(b"node-a"), _Node(b"node-b")
        a.peer, b.peer = b, a

        # Disjoint forks from the shared genesis (distinct timestamps → distinct hashes).
        gts = _PARAMS.genesis_timestamp
        _feed(a, _build_chain(genesis, 1, 2, ts0=gts + 100))  # A: height 2
        b_blocks = _build_chain(genesis, 1, 4, ts0=gts + 500)  # B: height 4
        _feed(b, b_blocks)

        assert a.chain.height() == 2
        assert b.chain.height() == 4

        # B announces its tip; A must pull the whole branch back to genesis and reorg.
        b_tip = b.chain._blocks.tip()
        assert b_tip is not None
        a.handlers[BlockInvPayload](b.id, BlockInvPayload(b_tip.block_hash, b_tip.height))

        assert a.chain.height() == 4
        assert a.chain._blocks.tip().block_hash == b_tip.block_hash  # type: ignore[union-attr]

    def test_sync_loop_rescans_missing_orphan_parents(self) -> None:
        """The background re-scan links a branch buffered as orphans (no inv needed)."""
        genesis = _PARAMS.build_genesis().block_hash
        a, b = _Node(b"node-a"), _Node(b"node-b")
        a.peer, b.peer = b, a

        gts = _PARAMS.genesis_timestamp
        _feed(a, _build_chain(genesis, 1, 1, ts0=gts + 100))  # A: height 1
        b_blocks = _build_chain(genesis, 1, 3, ts0=gts + 500)  # B: height 3
        _feed(b, b_blocks)

        # Inject only B's tip into A → buffered as an orphan (its parent is missing).
        a.chain.connect_block(b_blocks[-1], source="peer")
        assert a.chain.height() == 1
        assert a.chain._blocks.missing_orphan_parents()

        # The real background re-scan should re-ask for the missing parents until linked.
        a.tasks["sync_loop"]()

        assert a.chain.height() == 3
        b_tip = b.chain._blocks.tip()
        assert b_tip is not None
        assert a.chain._blocks.tip().block_hash == b_tip.block_hash  # type: ignore[union-attr]
