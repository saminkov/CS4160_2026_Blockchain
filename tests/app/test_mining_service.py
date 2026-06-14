from __future__ import annotations

from blockchain.adapters.block_store import InMemoryBlockStore
from blockchain.adapters.mempool import InMemoryMempool
from blockchain.adapters.system_clock import FakeClock
from blockchain.adapters.utxo_store import InMemoryUTXOStore
from blockchain.app.chain_service import ChainService
from blockchain.app.mining_service import MiningService
from blockchain.app.validation_service import ValidationService
from blockchain.core.codec import (
    TransferData,
    TransferInput,
    TransferOutput,
    decode_coinbase_data,
    encode_transfer_data,
)
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import Transaction
from blockchain.core.pow import search_nonce
from blockchain.ports.miner import OnFound

_MINER_PK = b"LibNaCLPK:miner"


class _FakeCrypto:
    @staticmethod
    def verify(_pk: bytes, _msg: bytes, _sig: bytes) -> bool:
        return True

    @staticmethod
    def sign(_privkey: bytes, _msg: bytes) -> bytes:
        return b"sig"


class _Job:
    def __init__(self, prefix: bytes, difficulty: int, generation: int, on_found: OnFound) -> None:
        self.prefix = prefix
        self.difficulty = difficulty
        self.generation = generation
        self.on_found = on_found


class _FakeMiner:
    """Records mine() calls; finish() computes a real nonce and delivers it on the loop."""

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

    @property
    def last(self) -> _Job:
        return self.jobs[-1]

    def finish(self, *, generation: int | None = None) -> None:
        job = self.last
        gen = job.generation if generation is None else generation
        nonce = search_nonce(job.prefix, job.difficulty, should_abort=lambda: False)
        assert nonce is not None
        job.on_found(nonce, gen)


def _build() -> tuple[
    MiningService, _FakeMiner, ChainService, InMemoryMempool, InMemoryUTXOStore, ConsensusParams
]:
    params = ConsensusParams.default()
    mempool = InMemoryMempool()
    utxo = InMemoryUTXOStore()
    blocks = InMemoryBlockStore()
    clock = FakeClock(params.genesis_timestamp + 10_000)
    miner = _FakeMiner()
    chain = ChainService(
        params,
        blocks,
        utxo,
        mempool,
        ValidationService(params, None, utxo, _FakeCrypto()),
        clock,
    )
    mining = MiningService(
        params,
        mempool,
        chain,
        miner,
        clock,
        _FakeCrypto(),
        utxo,
        miner_pubkey=_MINER_PK,
        miner_privkey=b"priv",
    )
    chain._on_tip_changed = mining.on_tip_changed
    return mining, miner, chain, mempool, utxo, params


def _coinbase_amount(tx: Transaction) -> int:
    return sum(out.amount for out in decode_coinbase_data(tx.data).outputs)


class TestMiningService:
    def test_first_candidate_on_genesis(self) -> None:
        _mining, miner, chain, _mempool, _utxo, params = _build()
        chain.initialize_genesis()

        assert len(miner.jobs) == 1
        assert miner.last.generation == 1
        genesis = params.build_genesis()
        assert miner.last.prefix[:32] == genesis.block_hash
        # Coinbase-only candidate while the mempool is empty.
        assert len(chain._mempool) == 0

    def test_found_block_connects_and_rebuilds(self) -> None:
        _mining, miner, chain, _mempool, _utxo, _params = _build()
        chain.initialize_genesis()

        miner.finish()

        assert chain.height() == 1
        # cancel-and-rebuild: a fresh job for the next height was dispatched.
        assert miner.last.generation == 2
        assert len(miner.jobs) == 2

    def test_stale_nonce_is_ignored(self) -> None:
        _mining, miner, chain, _mempool, _utxo, _params = _build()
        chain.initialize_genesis()

        miner.finish(generation=0)

        assert chain.height() == 0

    def test_data_carrier_included_zero_fee(self) -> None:
        _mining, miner, chain, mempool, _utxo, params = _build()
        carrier = Transaction(
            sender_key=b"LibNaCLPK:srv",
            data=b"server payload",
            timestamp=params.genesis_timestamp + 50,
            signature=b"sig",
        )
        mempool.add(carrier, fee=0)
        # First candidate (built on genesis connect) already sees the pooled tx.
        chain.initialize_genesis()
        miner.finish()

        block = chain._blocks.node_at(1)
        assert block is not None
        assert len(block.block.transactions) == 2
        assert _coinbase_amount(block.block.transactions[0]) == params.reward(1)

    def test_transfer_fee_added_to_coinbase(self) -> None:
        _mining, miner, chain, mempool, utxo, params = _build()
        premine = params.build_genesis().premine_utxos[0]
        utxo._utxos[premine.outpoint] = premine
        fee = 9
        data = encode_transfer_data(
            TransferData(
                inputs=(
                    TransferInput(
                        prev_txid=premine.outpoint.txid, output_index=premine.outpoint.index
                    ),
                ),
                outputs=(
                    TransferOutput(recipient_pubkey=b"LibNaCLPK:dst", amount=premine.amount - fee),
                ),
            )
        )
        transfer = Transaction(
            sender_key=premine.recipient_pubkey,
            data=data,
            timestamp=params.genesis_timestamp + 60,
            signature=b"sig",
        )
        mempool.add(transfer, fee=fee)
        chain.initialize_genesis()
        miner.finish()

        block = chain._blocks.node_at(1)
        assert block is not None
        assert len(block.block.transactions) == 2
        assert _coinbase_amount(block.block.transactions[0]) == params.reward(1) + fee

    def test_timestamp_strictly_increases_over_parent(self) -> None:
        params = ConsensusParams.default()
        mempool = InMemoryMempool()
        utxo = InMemoryUTXOStore()
        blocks = InMemoryBlockStore()
        # Clock behind the genesis timestamp -> candidate must still beat the parent.
        clock = FakeClock(params.genesis_timestamp - 1_000)
        miner = _FakeMiner()
        chain = ChainService(
            params,
            blocks,
            utxo,
            mempool,
            ValidationService(params, None, utxo, _FakeCrypto()),
            clock,
        )
        mining = MiningService(
            params,
            mempool,
            chain,
            miner,
            clock,
            _FakeCrypto(),
            utxo,
            miner_pubkey=_MINER_PK,
            miner_privkey=b"priv",
        )
        chain._on_tip_changed = mining.on_tip_changed
        chain.initialize_genesis()

        candidate_ts = mining._candidate_header.timestamp  # type: ignore[union-attr]
        assert candidate_ts == params.genesis_timestamp + 1
