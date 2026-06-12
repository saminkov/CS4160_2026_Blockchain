from __future__ import annotations

import pytest

from blockchain.adapters.utxo_store import InMemoryUTXOStore
from blockchain.core.codec import (
    CoinbaseData,
    CoinbaseOutput,
    TransferData,
    TransferInput,
    TransferOutput,
    encode_coinbase_data,
    encode_transfer_data,
)
from blockchain.core.entities import HASH_SIZE, Block, BlockHeader, Outpoint, Transaction
from blockchain.core.hashing import tx_hash
from blockchain.ports.stores import UTXOStorePort

# mypy conformance.
_u: UTXOStorePort = InMemoryUTXOStore()

_ZERO = b"\x00" * HASH_SIZE
_PK_A = b"LibNaCLPK:alice"
_PK_B = b"LibNaCLPK:bob"


def _header() -> BlockHeader:
    return BlockHeader(
        prev_hash=_ZERO, txs_hash=_ZERO, timestamp=1_700_000_000, difficulty=12, nonce=0
    )


def _tx(data: bytes, *, timestamp: int = 1_700_000_000) -> Transaction:
    return Transaction(sender_key=_PK_A, data=data, timestamp=timestamp, signature=b"sig")


def _coinbase(height: int, *, pk: bytes = _PK_A, amount: int = 100) -> Transaction:
    return _tx(
        encode_coinbase_data(
            CoinbaseData(height=height, outputs=(CoinbaseOutput(recipient_pubkey=pk, amount=amount),))
        )
    )


def _transfer(
    inputs: tuple[TransferInput, ...], outputs: tuple[TransferOutput, ...], *, ts: int = 1_700_000_000
) -> Transaction:
    return _tx(encode_transfer_data(TransferData(inputs=inputs, outputs=outputs)), timestamp=ts)


def _block(*txs: Transaction) -> Block:
    return Block(header=_header(), transactions=txs)


class TestApplyCoinbase:
    def test_coinbase_outputs_become_unspent_utxos(self) -> None:
        store = InMemoryUTXOStore()
        cb = _coinbase(1, amount=100)
        store.apply(_block(cb), height=1)
        op = Outpoint(txid=tx_hash(cb), index=0)
        utxo = store.get(op)
        assert utxo is not None
        assert utxo.amount == 100
        assert utxo.is_coinbase is True
        assert utxo.height_created == 1
        assert store.is_unspent(op) is True


class TestApplyTransfer:
    def test_transfer_spends_input_and_creates_outputs(self) -> None:
        store = InMemoryUTXOStore()
        cb = _coinbase(1, amount=100)
        store.apply(_block(cb), height=1)
        funded = Outpoint(txid=tx_hash(cb), index=0)

        transfer = _transfer(
            inputs=(TransferInput(prev_txid=funded.txid, output_index=0),),
            outputs=(TransferOutput(recipient_pubkey=_PK_B, amount=100),),
        )
        store.apply(_block(transfer), height=2)

        assert store.is_unspent(funded) is False
        new_op = Outpoint(txid=tx_hash(transfer), index=0)
        new_utxo = store.get(new_op)
        assert new_utxo is not None
        assert new_utxo.recipient_pubkey == _PK_B
        assert new_utxo.is_coinbase is False

    def test_apply_returns_matching_undo_record(self) -> None:
        store = InMemoryUTXOStore()
        cb = _coinbase(1, amount=100)
        store.apply(_block(cb), height=1)
        funded = Outpoint(txid=tx_hash(cb), index=0)
        spent_utxo = store.get(funded)

        transfer = _transfer(
            inputs=(TransferInput(prev_txid=funded.txid, output_index=0),),
            outputs=(TransferOutput(recipient_pubkey=_PK_B, amount=100),),
        )
        undo = store.apply(_block(transfer), height=2)
        assert undo.spent == (spent_utxo,)
        assert undo.created == (Outpoint(txid=tx_hash(transfer), index=0),)

    def test_missing_input_raises(self) -> None:
        store = InMemoryUTXOStore()
        transfer = _transfer(
            inputs=(TransferInput(prev_txid=b"\x11" * HASH_SIZE, output_index=0),),
            outputs=(TransferOutput(recipient_pubkey=_PK_B, amount=1),),
        )
        with pytest.raises(ValueError):
            store.apply(_block(transfer), height=1)


class TestRollback:
    def test_rollback_restores_pre_state(self) -> None:
        store = InMemoryUTXOStore()
        cb = _coinbase(1, amount=100)
        store.apply(_block(cb), height=1)
        funded = Outpoint(txid=tx_hash(cb), index=0)
        before = store.get(funded)

        transfer = _transfer(
            inputs=(TransferInput(prev_txid=funded.txid, output_index=0),),
            outputs=(TransferOutput(recipient_pubkey=_PK_B, amount=100),),
        )
        undo = store.apply(_block(transfer), height=2)
        store.rollback(undo)

        assert store.get(funded) == before
        assert store.is_unspent(funded) is True
        assert store.get(Outpoint(txid=tx_hash(transfer), index=0)) is None

    def test_intra_block_spend_then_rollback(self) -> None:
        store = InMemoryUTXOStore()
        cb = _coinbase(1, amount=100)
        store.apply(_block(cb), height=1)
        funded = Outpoint(txid=tx_hash(cb), index=0)
        before = store.get(funded)

        # tx1 spends the coinbase, creating an output; tx2 spends tx1's output.
        tx1 = _transfer(
            inputs=(TransferInput(prev_txid=funded.txid, output_index=0),),
            outputs=(TransferOutput(recipient_pubkey=_PK_B, amount=100),),
            ts=1_700_000_001,
        )
        op_internal = Outpoint(txid=tx_hash(tx1), index=0)
        tx2 = _transfer(
            inputs=(TransferInput(prev_txid=op_internal.txid, output_index=0),),
            outputs=(TransferOutput(recipient_pubkey=_PK_A, amount=100),),
            ts=1_700_000_002,
        )
        undo = store.apply(_block(tx1, tx2), height=2)
        store.rollback(undo)

        # internal outpoint must not survive; the original coinbase input is restored.
        assert store.get(op_internal) is None
        assert store.get(Outpoint(txid=tx_hash(tx2), index=0)) is None
        assert store.get(funded) == before


class TestDataCarrierAndView:
    def test_data_carrier_has_no_utxo_effect(self) -> None:
        store = InMemoryUTXOStore()
        undo = store.apply(_block(_tx(b"arbitrary-server-payload")), height=1)
        assert undo.spent == ()
        assert undo.created == ()

    def test_read_view_is_independent_snapshot(self) -> None:
        store = InMemoryUTXOStore()
        cb = _coinbase(1, amount=100)
        store.apply(_block(cb), height=1)
        op = Outpoint(txid=tx_hash(cb), index=0)

        view = store.read_view()
        # Mutate the live store after taking the snapshot.
        store.rollback(store.apply(_block(_coinbase(2, pk=_PK_B)), height=2))
        store.apply(
            _block(
                _transfer(
                    inputs=(TransferInput(prev_txid=op.txid, output_index=0),),
                    outputs=(TransferOutput(recipient_pubkey=_PK_B, amount=100),),
                )
            ),
            height=2,
        )
        # Snapshot still sees the original coinbase UTXO as unspent.
        assert view.is_unspent(op) is True
        assert store.is_unspent(op) is False

    def test_unknown_outpoint(self) -> None:
        store = InMemoryUTXOStore()
        op = Outpoint(txid=b"\x22" * HASH_SIZE, index=0)
        assert store.get(op) is None
        assert store.is_unspent(op) is False
