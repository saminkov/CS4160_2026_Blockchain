from __future__ import annotations

from blockchain.adapters.block_store import InMemoryBlockStore
from blockchain.adapters.mempool import InMemoryMempool
from blockchain.adapters.system_clock import FakeClock
from blockchain.adapters.utxo_store import InMemoryUTXOStore
from blockchain.app.chain_service import ChainService, block_response_sentinel
from blockchain.app.validation_service import ValidationService
from blockchain.core.codec import CoinbaseData, CoinbaseOutput, encode_coinbase_data
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import Block, BlockHeader, Transaction
from blockchain.core.hashing import block_hash, header_mining_prefix, tx_hash, txs_hash
from blockchain.core.pow import search_nonce

_PK_A = b"LibNaCLPK:alice"


class _FakeCrypto:
    verify = staticmethod(lambda _pk, _msg, _sig: True)


def _coinbase(height: int, amount: int, *, timestamp: int) -> Transaction:
    data = encode_coinbase_data(
        CoinbaseData(height=height, outputs=(CoinbaseOutput(recipient_pubkey=_PK_A, amount=amount),))
    )
    return Transaction(sender_key=_PK_A, data=data, timestamp=timestamp, signature=b"sig")


def _mined_block(
    transactions: tuple[Transaction, ...],
    *,
    prev_hash: bytes,
    timestamp: int,
) -> Block:
    params = ConsensusParams.default()
    header = BlockHeader(
        prev_hash=prev_hash,
        txs_hash=txs_hash(transactions),
        timestamp=timestamp,
        difficulty=params.difficulty_bits,
        nonce=0,
    )
    nonce = search_nonce(
        header_mining_prefix(header),
        params.difficulty_bits,
        should_abort=lambda: False,
    )
    assert nonce is not None
    return Block(
        header=BlockHeader(
            prev_hash=prev_hash,
            txs_hash=txs_hash(transactions),
            timestamp=timestamp,
            difficulty=params.difficulty_bits,
            nonce=nonce,
        ),
        transactions=transactions,
    )


def _service() -> ChainService:
    params = ConsensusParams.default()
    utxo = InMemoryUTXOStore()
    return ChainService(
        params,
        InMemoryBlockStore(),
        utxo,
        InMemoryMempool(),
        ValidationService(params, None, utxo, _FakeCrypto()),
        FakeClock(params.genesis_timestamp + 10_000),
    )


def _ts(params: ConsensusParams, offset: int) -> int:
    return params.genesis_timestamp + offset


class TestChainService:
    def test_connect_and_queries(self) -> None:
        params = ConsensusParams.default()
        service = _service()
        genesis = params.build_genesis()
        service.initialize_genesis()

        assert service.height() == 0
        assert service.block_response_at(0).block_hash == genesis.block_hash
        assert service.block_response_at(99) == block_response_sentinel(99)

        block1 = _mined_block(
            (_coinbase(1, params.reward(1), timestamp=_ts(params, 100)),),
            prev_hash=genesis.block_hash,
            timestamp=_ts(params, 100),
        )
        assert service.connect_block(block1, source="test").ok
        assert service.height() == 1
        assert service.connect_block(block1, source="test").reason == "already known"

    def test_reorg_restores_orphaned_mempool_txs(self) -> None:
        params = ConsensusParams.default()
        service = _service()
        service.initialize_genesis()
        genesis = params.build_genesis()

        block1 = _mined_block(
            (_coinbase(1, params.reward(1), timestamp=_ts(params, 100)),),
            prev_hash=genesis.block_hash,
            timestamp=_ts(params, 100),
        )
        carrier = Transaction(
            sender_key=_PK_A,
            data=b"hello from mempool",
            timestamp=_ts(params, 150),
            signature=b"sig",
        )
        service._mempool.add(carrier, fee=1)
        block2 = _mined_block(
            (_coinbase(2, params.reward(2), timestamp=_ts(params, 200)), carrier),
            prev_hash=block_hash(block1.header),
            timestamp=_ts(params, 200),
        )
        service.connect_block(block1, source="test")
        service.connect_block(block2, source="test")
        assert not service._mempool.contains(tx_hash(carrier))

        alt2 = _mined_block(
            (_coinbase(2, params.reward(2), timestamp=_ts(params, 250)),),
            prev_hash=block_hash(block1.header),
            timestamp=_ts(params, 250),
        )
        block3 = _mined_block(
            (_coinbase(3, params.reward(3), timestamp=_ts(params, 300)),),
            prev_hash=block_hash(alt2.header),
            timestamp=_ts(params, 300),
        )
        service.connect_block(alt2, source="test")
        service.connect_block(block3, source="test")

        assert service.height() == 3
        assert service._mempool.contains(tx_hash(carrier))
