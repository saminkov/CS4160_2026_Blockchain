from __future__ import annotations

from blockchain.adapters.block_store import InMemoryBlockStore
from blockchain.adapters.mempool import InMemoryMempool
from blockchain.adapters.system_clock import FakeClock
from blockchain.adapters.utxo_store import InMemoryUTXOStore
from blockchain.app.chain_service import ChainService, block_response_sentinel
from blockchain.app.validation_service import ValidationService
from blockchain.core.codec import (
    CoinbaseData,
    CoinbaseOutput,
    TransferData,
    TransferInput,
    TransferOutput,
    encode_coinbase_data,
    encode_transfer_data,
)
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import UTXO, Block, BlockHeader, Transaction
from blockchain.core.hashing import block_hash, header_mining_prefix, tx_hash, txs_hash
from blockchain.core.pow import search_nonce

_PK_A = b"LibNaCLPK:alice"


class _FakeCrypto:
    verify = staticmethod(lambda _pk, _msg, _sig: True)


def _coinbase(height: int, amount: int, *, timestamp: int) -> Transaction:
    data = encode_coinbase_data(
        CoinbaseData(
            height=height, outputs=(CoinbaseOutput(recipient_pubkey=_PK_A, amount=amount),)
        )
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


def _transfer(utxo: UTXO, *, timestamp: int, out_amount: int | None = None) -> Transaction:
    """A fee-0 self-transfer that spends ``utxo`` (signature is faked-valid)."""
    amount = utxo.amount if out_amount is None else out_amount
    data = encode_transfer_data(
        TransferData(
            inputs=(TransferInput(prev_txid=utxo.outpoint.txid, output_index=utxo.outpoint.index),),
            outputs=(TransferOutput(recipient_pubkey=utxo.recipient_pubkey, amount=amount),),
        )
    )
    return Transaction(
        sender_key=utxo.recipient_pubkey, data=data, timestamp=timestamp, signature=b"sig"
    )


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

    def test_intra_block_double_spend_rejected(self) -> None:
        params = ConsensusParams.default()
        service = _service()
        service.initialize_genesis()
        genesis = params.build_genesis()
        premine = genesis.premine_utxos[0]  # spendable at height 0 (maturity-exempt)

        t1 = _transfer(premine, timestamp=_ts(params, 150))
        t2 = _transfer(premine, timestamp=_ts(params, 151))  # distinct tx, same input
        block1 = _mined_block(
            (_coinbase(1, params.reward(1), timestamp=_ts(params, 100)), t1, t2),
            prev_hash=genesis.block_hash,
            timestamp=_ts(params, 100),
        )

        result = service.connect_block(block1, source="test")
        assert not result.ok
        assert result.reason == "double spend within block"
        assert service.height() == 0

    def test_respend_in_child_block_rejected(self) -> None:
        params = ConsensusParams.default()
        service = _service()
        service.initialize_genesis()
        genesis = params.build_genesis()
        premine = genesis.premine_utxos[0]

        t1 = _transfer(premine, timestamp=_ts(params, 150))
        block1 = _mined_block(
            (_coinbase(1, params.reward(1), timestamp=_ts(params, 100)), t1),
            prev_hash=genesis.block_hash,
            timestamp=_ts(params, 100),
        )
        assert service.connect_block(block1, source="test").ok
        assert service.height() == 1

        # block2 re-spends the same premine outpoint, now consumed by block1.
        t2 = _transfer(premine, timestamp=_ts(params, 250))
        block2 = _mined_block(
            (_coinbase(2, params.reward(2), timestamp=_ts(params, 200)), t2),
            prev_hash=block_hash(block1.header),
            timestamp=_ts(params, 200),
        )
        result = service.connect_block(block2, source="test")
        assert not result.ok
        assert result.reason == "transfer input references missing UTXO"
        assert service.height() == 1
