from __future__ import annotations

from typing import Any

from blockchain.adapters.mempool import InMemoryMempool
from blockchain.adapters.payloads import (
    SubmitTransactionPayload,
    SubmitTransactionResponsePayload,
    TxGossipPayload,
)
from blockchain.adapters.utxo_store import InMemoryUTXOStore
from blockchain.app.mempool_service import MempoolService
from blockchain.core.codec import (
    TransferData,
    TransferInput,
    TransferOutput,
    encode_transfer_data,
)
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import UTXO, Outpoint, Transaction
from blockchain.core.hashing import tx_hash


class _FakeCrypto:
    def __init__(self, *, valid: bool = True) -> None:
        self.valid = valid

    def verify(self, _pk: bytes, _msg: bytes, _sig: bytes) -> bool:
        return self.valid


class _FakeNetwork:
    """Records handlers, sends and broadcasts (the NetworkPort surface used here)."""

    def __init__(self) -> None:
        self.handlers: dict[type, Any] = {}
        self.sent: list[tuple[bytes, Any]] = []
        self.broadcasts: list[Any] = []

    def register_handler(self, payload_cls: type, handler: Any) -> None:
        self.handlers[payload_cls] = handler

    def register_task(self, name: str, fn: Any, interval: float) -> None:  # pragma: no cover
        pass

    def send(self, peer: bytes, payload: Any) -> None:
        self.sent.append((peer, payload))

    def broadcast_members(self, payload: Any) -> None:
        self.broadcasts.append(payload)

    def members_online(self) -> set[bytes]:  # pragma: no cover - unused
        return set()


class _StubChain:
    def __init__(self, height: int = 0) -> None:
        self._height = height

    def height(self) -> int:
        return self._height


def _data_carrier(timestamp: int = 1) -> Transaction:
    return Transaction(
        sender_key=b"LibNaCLPK:alice",
        data=b"hello on-chain",
        timestamp=timestamp,
        signature=b"sig",
    )


def _transfer_tx(utxo: UTXO, *, out_amount: int | None = None, timestamp: int = 1) -> Transaction:
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


def _make(
    *, valid_sig: bool = True
) -> tuple[MempoolService, _FakeNetwork, InMemoryMempool, InMemoryUTXOStore, ConsensusParams]:
    params = ConsensusParams.default()
    net = _FakeNetwork()
    mempool = InMemoryMempool()
    utxo = InMemoryUTXOStore()
    crypto = _FakeCrypto(valid=valid_sig)
    svc = MempoolService(mempool, net, crypto, params, utxo, _StubChain())
    return svc, net, mempool, utxo, params


def _submit(net: _FakeNetwork, peer: bytes, tx: Transaction) -> None:
    payload = SubmitTransactionPayload(tx.sender_key, tx.data, tx.timestamp, tx.signature)
    net.handlers[SubmitTransactionPayload](peer, payload)


class TestMempoolService:
    def test_data_carrier_accepted_and_acked(self) -> None:
        _svc, net, mempool, _utxo, params = _make()
        tx = _data_carrier()
        _submit(net, params.member_pubkeys[0], tx)

        assert mempool.contains(tx_hash(tx))
        responses = [p for _peer, p in net.sent if isinstance(p, SubmitTransactionResponsePayload)]
        assert responses and responses[-1].success is True
        assert responses[-1].message == "ok"

    def test_bad_signature_rejected(self) -> None:
        _svc, net, mempool, _utxo, params = _make(valid_sig=False)
        tx = _data_carrier()
        _submit(net, params.member_pubkeys[0], tx)

        assert not mempool.contains(tx_hash(tx))
        responses = [p for _peer, p in net.sent if isinstance(p, SubmitTransactionResponsePayload)]
        assert responses and responses[-1].success is False
        assert responses[-1].message == "bad signature"

    def test_gossip_rebroadcasts_to_other_members(self) -> None:
        _svc, net, mempool, _utxo, params = _make()
        tx = _data_carrier()
        sender = params.member_pubkeys[0]
        net.handlers[TxGossipPayload](
            sender, TxGossipPayload(tx.sender_key, tx.data, tx.timestamp, tx.signature)
        )

        assert mempool.contains(tx_hash(tx))
        gossip_targets = {peer for peer, p in net.sent if isinstance(p, TxGossipPayload)}
        assert gossip_targets == {m for m in params.member_pubkeys if m != sender}

    def test_transfer_spending_unknown_utxo_rejected(self) -> None:
        _svc, net, mempool, _utxo, params = _make()
        ghost = UTXO(
            outpoint=Outpoint(txid=b"\x00" * 32, index=0),
            recipient_pubkey=b"LibNaCLPK:nobody",
            amount=1_000,
            height_created=0,
            is_coinbase=False,
        )
        tx = _transfer_tx(ghost)
        _submit(net, params.member_pubkeys[0], tx)

        assert not mempool.contains(tx_hash(tx))
        responses = [p for _peer, p in net.sent if isinstance(p, SubmitTransactionResponsePayload)]
        assert responses and responses[-1].success is False
        assert responses[-1].message == "transfer spends missing or spent UTXO"

    def test_valid_transfer_accepted(self) -> None:
        _svc, net, mempool, utxo, params = _make()
        genesis = params.build_genesis()
        utxo.apply(genesis.block, 0)  # seed the premine UTXOs
        premine = genesis.premine_utxos[0]

        tx = _transfer_tx(premine)  # fee-0 self-transfer of a spendable premine UTXO
        _submit(net, params.member_pubkeys[0], tx)

        assert mempool.contains(tx_hash(tx))
        responses = [p for _peer, p in net.sent if isinstance(p, SubmitTransactionResponsePayload)]
        assert responses and responses[-1].success is True
