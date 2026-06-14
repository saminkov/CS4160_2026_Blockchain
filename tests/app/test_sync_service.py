from __future__ import annotations

from blockchain.adapters.block_store import InMemoryBlockStore
from blockchain.adapters.mempool import InMemoryMempool
from blockchain.adapters.system_clock import FakeClock
from blockchain.adapters.utxo_store import InMemoryUTXOStore
from blockchain.app.chain_service import ChainService
from blockchain.app.mining_service import MiningService
from blockchain.app.sync_service import SyncService
from blockchain.app.validation_service import ValidationService
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import Block
from blockchain.core.pow import search_nonce
from blockchain.ports.miner import OnFound

_PEER = b"LibNaCLPK:peer"
_MINER_PK = b"LibNaCLPK:miner"


class _FakeCrypto:
    @staticmethod
    def verify(_pk: bytes, _msg: bytes, _sig: bytes) -> bool:
        return True

    @staticmethod
    def sign(_privkey: bytes, _msg: bytes) -> bytes:
        return b"sig"


class _FakeNetwork:
    """Records every block-sync verb SyncService drives."""

    def __init__(self) -> None:
        self.announced: list[tuple[bytes, int]] = []
        self.data_requests: list[tuple[bytes, bytes]] = []
        self.blocks_sent: list[tuple[bytes, Block, int]] = []
        self.not_founds: list[tuple[bytes, int]] = []
        self.height_requests: list[tuple[bytes, int]] = []
        self.members: set[bytes] = {_PEER}

    def announce_block(self, block_hash: bytes, height: int) -> None:
        self.announced.append((block_hash, height))

    def request_block_data(self, peer: bytes, block_hash: bytes) -> None:
        self.data_requests.append((peer, block_hash))

    def send_block(self, peer: bytes, block: Block, height: int) -> None:
        self.blocks_sent.append((peer, block, height))

    def send_block_not_found(self, peer: bytes, height: int) -> None:
        self.not_founds.append((peer, height))

    def request_block_by_height(self, peer: bytes, height: int) -> None:
        self.height_requests.append((peer, height))

    def members_online(self) -> set[bytes]:
        return self.members


class _Job:
    def __init__(self, prefix: bytes, difficulty: int, generation: int, on_found: OnFound) -> None:
        self.prefix = prefix
        self.difficulty = difficulty
        self.generation = generation
        self.on_found = on_found


class _FakeMiner:
    """Records mine() calls; finish() computes a real nonce and delivers it."""

    def __init__(self) -> None:
        self.jobs: list[_Job] = []

    def mine(
        self, header_prefix: bytes, difficulty: int, generation: int, on_found: OnFound
    ) -> None:
        self.jobs.append(_Job(header_prefix, difficulty, generation, on_found))

    def cancel(self) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def finish(self) -> None:
        job = self.jobs[-1]
        nonce = search_nonce(job.prefix, job.difficulty, should_abort=lambda: False)
        assert nonce is not None
        job.on_found(nonce, job.generation)


def _new_chain(params: ConsensusParams) -> tuple[ChainService, InMemoryBlockStore]:
    blocks = InMemoryBlockStore()
    utxo = InMemoryUTXOStore()
    mempool = InMemoryMempool()
    clock = FakeClock(params.genesis_timestamp + 10_000)
    chain = ChainService(
        params,
        blocks,
        utxo,
        mempool,
        ValidationService(params, None, utxo, _FakeCrypto()),
        clock,
    )
    return chain, blocks


def _produce_blocks(params: ConsensusParams, count: int) -> list[Block]:
    """Mine ``count`` coinbase-only blocks above genesis with valid PoW and return them."""
    chain, blocks = _new_chain(params)
    miner = _FakeMiner()
    mining = MiningService(
        params,
        InMemoryMempool(),
        chain,
        miner,
        FakeClock(params.genesis_timestamp + 10_000),
        _FakeCrypto(),
        InMemoryUTXOStore(),
        miner_pubkey=_MINER_PK,
        miner_privkey=b"priv",
    )
    chain._on_tip_changed = mining.on_tip_changed
    chain.initialize_genesis()
    for _ in range(count):
        miner.finish()
    return [blocks.node_at(h).block for h in range(1, count + 1)]  # type: ignore[union-attr]


def _build() -> tuple[SyncService, _FakeNetwork, ChainService, InMemoryBlockStore, ConsensusParams]:
    params = ConsensusParams.default()
    chain, blocks = _new_chain(params)
    net = _FakeNetwork()
    sync = SyncService(net, blocks, chain)
    return sync, net, chain, blocks, params


class TestSyncService:
    def test_inv_unknown_requests_data(self) -> None:
        sync, net, _chain, _blocks, _params = _build()

        sync.on_block_inv(_PEER, b"\xab" * 32, height=5)

        assert net.data_requests == [(_PEER, b"\xab" * 32)]

    def test_inv_known_requests_nothing(self) -> None:
        sync, net, chain, blocks, _params = _build()
        chain.initialize_genesis()
        genesis = blocks.tip()
        assert genesis is not None

        sync.on_block_inv(_PEER, genesis.block_hash, height=0)

        assert net.data_requests == []

    def test_serve_block_data_known(self) -> None:
        sync, net, chain, blocks, _params = _build()
        chain.initialize_genesis()
        genesis = blocks.tip()
        assert genesis is not None

        sync.serve_block_data(_PEER, genesis.block_hash)

        assert len(net.blocks_sent) == 1
        peer, block, height = net.blocks_sent[0]
        assert peer == _PEER
        assert block == genesis.block
        assert height == 0

    def test_serve_block_data_unknown_drops(self) -> None:
        sync, net, _chain, _blocks, _params = _build()

        sync.serve_block_data(_PEER, b"\xcd" * 32)

        assert net.blocks_sent == []

    def test_on_block_valid_child_connects(self) -> None:
        sync, _net, chain, _blocks, params = _build()
        chain.initialize_genesis()
        block1 = _produce_blocks(params, 1)[0]

        sync._needed.add(1)
        sync.on_block(_PEER, block1, height=1)

        assert chain.height() == 1
        assert 1 not in sync._needed

    def test_on_block_orphan_pulls_parent(self) -> None:
        sync, net, chain, _blocks, params = _build()
        chain.initialize_genesis()
        # Height-2 block whose parent (height 1) the consumer has never seen.
        block2 = _produce_blocks(params, 2)[1]

        sync.on_block(_PEER, block2, height=2)

        assert chain.height() == 0  # still only genesis
        assert 1 in sync._needed
        assert net.height_requests == [(_PEER, 1)]

    def test_serve_block_by_height_known(self) -> None:
        sync, net, chain, _blocks, _params = _build()
        chain.initialize_genesis()

        sync.serve_block_by_height(_PEER, 0)

        assert len(net.blocks_sent) == 1
        assert net.not_founds == []

    def test_serve_block_by_height_unknown_sentinel(self) -> None:
        sync, net, chain, _blocks, _params = _build()
        chain.initialize_genesis()

        sync.serve_block_by_height(_PEER, 99)

        assert net.blocks_sent == []
        assert net.not_founds == [(_PEER, 99)]

    def test_rescan_rerequests_and_clears(self) -> None:
        sync, net, chain, blocks, params = _build()
        chain.initialize_genesis()
        block1 = _produce_blocks(params, 1)[0]

        # Two pending heights: one we still lack (1), one already linked after we connect it (0).
        sync._needed = {1}
        sync.rescan()
        assert (_PEER, 1) in net.height_requests
        assert 1 in sync._needed  # still missing

        # Now learn height 1; re-scan must drop it from the pending set.
        chain.connect_block(block1, source="sync")
        net.height_requests.clear()
        sync.rescan()
        assert 1 not in sync._needed
        assert (_PEER, 1) not in net.height_requests

    def test_rescan_no_members_is_noop(self) -> None:
        sync, net, chain, _blocks, _params = _build()
        chain.initialize_genesis()
        net.members = set()
        sync._needed = {1}

        sync.rescan()

        assert net.height_requests == []

    def test_announce_tip_broadcasts_inv(self) -> None:
        sync, net, chain, blocks, _params = _build()
        chain.initialize_genesis()
        tip = blocks.tip()
        assert tip is not None

        sync.announce_tip(tip)

        assert net.announced == [(tip.block_hash, tip.height)]
