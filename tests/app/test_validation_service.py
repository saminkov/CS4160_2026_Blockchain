from __future__ import annotations

from concurrent.futures import Future
from unittest.mock import MagicMock

from blockchain.adapters.ecc_crypto import ECCryptoAdapter
from blockchain.adapters.utxo_store import InMemoryUTXOStore
from blockchain.app.validation_service import (
    DEFAULT_OFFLOAD_TX_THRESHOLD,
    ValidationService,
    _phase_a_task,
)
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
from blockchain.core.entities import HASH_SIZE, Block, BlockHeader, Outpoint, Result, Transaction
from blockchain.core.hashing import block_hash, header_mining_prefix, tx_hash, txs_hash
from blockchain.core.pow import search_nonce
from blockchain.core.validation import MAX_BLOCK_BYTES, validate_block_stateful


_ZERO = b"\x00" * HASH_SIZE
_PK_A = b"LibNaCLPK:alice"
_PK_B = b"LibNaCLPK:bob"


def _always_valid(_pubkey: bytes, _message: bytes, _signature: bytes) -> bool:
    return True


class _FakeCrypto:
    verify = staticmethod(_always_valid)


def _header(
    *,
    prev_hash: bytes = _ZERO,
    txs_hash_value: bytes = _ZERO,
    timestamp: int = 1_700_000_100,
    difficulty: int = 12,
    nonce: int = 0,
) -> BlockHeader:
    return BlockHeader(
        prev_hash=prev_hash,
        txs_hash=txs_hash_value,
        timestamp=timestamp,
        difficulty=difficulty,
        nonce=nonce,
    )


def _tx(data: bytes, *, timestamp: int = 1_700_000_100) -> Transaction:
    return Transaction(sender_key=_PK_A, data=data, timestamp=timestamp, signature=b"sig")


def _coinbase(height: int, amount: int) -> Transaction:
    data = encode_coinbase_data(
        CoinbaseData(height=height, outputs=(CoinbaseOutput(recipient_pubkey=_PK_A, amount=amount),))
    )
    return _tx(data)


def _service(*, pool: MagicMock | None = None, threshold: int = DEFAULT_OFFLOAD_TX_THRESHOLD) -> ValidationService:
    return ValidationService(
        ConsensusParams.default(),
        pool,
        InMemoryUTXOStore(),
        _FakeCrypto(),
        offload_tx_threshold=threshold,
    )


def _mined_block(transactions: tuple[Transaction, ...], *, prev_hash: bytes, timestamp: int) -> Block:
    params = ConsensusParams.default()
    header = _header(
        prev_hash=prev_hash,
        txs_hash_value=txs_hash(transactions),
        timestamp=timestamp,
        difficulty=params.difficulty_bits,
    )
    nonce = search_nonce(
        header_mining_prefix(header),
        params.difficulty_bits,
        should_abort=lambda: False,
    )
    assert nonce is not None
    return Block(
        header=_header(
            prev_hash=prev_hash,
            txs_hash_value=txs_hash(transactions),
            timestamp=timestamp,
            difficulty=params.difficulty_bits,
            nonce=nonce,
        ),
        transactions=transactions,
    )


class TestValidationService:
    def test_genesis_passes_both_phases(self) -> None:
        params = ConsensusParams.default()
        genesis = params.build_genesis()
        store = InMemoryUTXOStore()
        store.apply(genesis.block, height=0)
        service = ValidationService(params, None, store, _FakeCrypto())
        result = service.validate_block(
            genesis.block,
            height=0,
            parent_header=None,
            parent_block_hash=None,
            now=params.genesis_timestamp,
        )
        assert result.ok

    def test_phase_b_rejects_broken_prev_hash_link(self) -> None:
        params = ConsensusParams.default()
        genesis = params.build_genesis()
        parent_hash = genesis.block_hash
        parent_header = genesis.block.header
        coinbase = _coinbase(1, params.reward(1))
        block = _mined_block((coinbase,), prev_hash=_ZERO, timestamp=parent_header.timestamp + 1)
        service = _service()
        result = service.validate_phase_b(
            block,
            height=1,
            parent_header=parent_header,
            parent_block_hash=parent_hash,
            now=block.header.timestamp,
        )
        assert not result.ok

    def test_phase_b_rejects_immature_coinbase_spend(self) -> None:
        params = ConsensusParams.default()
        store = InMemoryUTXOStore()
        reward = params.reward(1)
        coinbase = _coinbase(1, reward)
        parent_block = Block(
            header=_header(txs_hash_value=txs_hash((coinbase,)), timestamp=1_700_000_100),
            transactions=(coinbase,),
        )
        parent_hash = block_hash(parent_block.header)
        store.apply(parent_block, height=1)
        funded = Outpoint(txid=tx_hash(coinbase), index=0)
        transfer = _tx(
            encode_transfer_data(
                TransferData(
                    inputs=(TransferInput(prev_txid=funded.txid, output_index=0),),
                    outputs=(TransferOutput(recipient_pubkey=_PK_B, amount=reward),),
                )
            ),
            timestamp=1_700_000_200,
        )
        fee_coinbase = _coinbase(2, params.reward(2))
        block = Block(
            header=_header(
                prev_hash=parent_hash,
                txs_hash_value=txs_hash((fee_coinbase, transfer)),
                timestamp=1_700_000_200,
            ),
            transactions=(fee_coinbase, transfer),
        )
        result = validate_block_stateful(
            block,
            height=2,
            params=params,
            utxo_view=store.read_view(),
            parent_header=parent_block.header,
            parent_block_hash=parent_hash,
            now=1_700_000_200,
        )
        assert not result.ok
        assert result.reason == "immature coinbase spend"

    def test_phase_a_inline_when_below_threshold(self) -> None:
        service = _service(threshold=100)
        coinbase = _coinbase(1, 100)
        block = _mined_block((coinbase,), prev_hash=_ZERO, timestamp=1_700_000_100)
        assert service.validate_phase_a(block, height=1).ok

    def test_phase_a_offloads_above_threshold(self) -> None:
        params = ConsensusParams.default()
        coinbase = _coinbase(1, params.reward(1))
        carriers = tuple(
            Transaction(
                sender_key=_PK_A,
                data=f"carrier-{index}".encode(),
                timestamp=1_700_000_100,
                signature=b"sig",
            )
            for index in range(DEFAULT_OFFLOAD_TX_THRESHOLD - 1)
        )
        txs = (coinbase,) + carriers
        block = _mined_block(txs, prev_hash=_ZERO, timestamp=1_700_000_100)
        future: Future[Result] = Future()
        future.set_result(Result(ok=True, reason=""))
        pool = MagicMock()
        pool.submit.return_value = future
        service = ValidationService(
            params,
            pool,
            InMemoryUTXOStore(),
            ECCryptoAdapter(),
            offload_tx_threshold=DEFAULT_OFFLOAD_TX_THRESHOLD,
        )
        result = service.validate_phase_a(block, height=1)
        assert result.ok
        pool.submit.assert_called_once_with(_phase_a_task, block, 1, params, MAX_BLOCK_BYTES)
