from __future__ import annotations

from blockchain.adapters.block_store import InMemoryBlockStore
from blockchain.adapters.mempool import InMemoryMempool
from blockchain.adapters.system_clock import FakeClock
from blockchain.adapters.utxo_store import InMemoryUTXOStore
from blockchain.app.mempool_service import MempoolService
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
from blockchain.core.entities import Transaction
from blockchain.core.hashing import tx_hash


class _FakeCrypto:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid

    def verify(self, _pk: bytes, _msg: bytes, _sig: bytes) -> bool:
        return self.valid


class _FakeNetwork:
    def __init__(self) -> None:
        self.gossiped: list[Transaction] = []

    def gossip_tx(self, tx: Transaction) -> None:
        self.gossiped.append(tx)


def _carrier(*, timestamp: int, data: bytes = b"server payload") -> Transaction:
    return Transaction(
        sender_key=b"LibNaCLPK:srv", data=data, timestamp=timestamp, signature=b"sig"
    )


def _make_service(
    *,
    crypto: _FakeCrypto | None = None,
    network: _FakeNetwork | None = None,
    now_offset: int = 0,
) -> tuple[MempoolService, InMemoryMempool, InMemoryUTXOStore, _FakeNetwork]:
    params = ConsensusParams.default()
    mempool = InMemoryMempool()
    utxo = InMemoryUTXOStore()
    net = network or _FakeNetwork()
    service = MempoolService(
        params,
        mempool,
        crypto or _FakeCrypto(),
        net,
        FakeClock(params.genesis_timestamp + now_offset),
        InMemoryBlockStore(),
        utxo,
    )
    return service, mempool, utxo, net


class TestMempoolService:
    def test_data_carrier_accepted_and_gossiped(self) -> None:
        service, mempool, _utxo, net = _make_service()
        tx = _carrier(timestamp=ConsensusParams.default().genesis_timestamp)

        success, h, message = service.submit_tx(tx, from_server=True)

        assert success is True
        assert h == tx_hash(tx)
        assert message == "accepted"
        assert mempool.contains(h)
        assert net.gossiped == [tx]

    def test_invalid_signature_rejected(self) -> None:
        service, mempool, _utxo, net = _make_service(crypto=_FakeCrypto(valid=False))
        tx = _carrier(timestamp=ConsensusParams.default().genesis_timestamp)

        success, h, _message = service.submit_tx(tx, from_server=True)

        assert success is False
        assert not mempool.contains(h)
        assert net.gossiped == []

    def test_duplicate_resubmit_not_regossiped(self) -> None:
        service, _mempool, _utxo, net = _make_service()
        tx = _carrier(timestamp=ConsensusParams.default().genesis_timestamp)

        service.submit_tx(tx, from_server=True)
        success, _h, message = service.submit_tx(tx, from_server=True)

        assert success is True
        assert message == "already in mempool"
        assert net.gossiped == [tx]

    def test_oversized_data_rejected(self) -> None:
        service, mempool, _utxo, _net = _make_service()
        tx = _carrier(
            timestamp=ConsensusParams.default().genesis_timestamp,
            data=b"x" * (65_536 + 1),
        )

        success, h, _message = service.submit_tx(tx, from_server=True)

        assert success is False
        assert not mempool.contains(h)

    def test_future_timestamp_rejected(self) -> None:
        params = ConsensusParams.default()
        service, mempool, _utxo, _net = _make_service()
        tx = _carrier(timestamp=params.genesis_timestamp + params.timestamp_tolerance_seconds + 1)

        success, h, _message = service.submit_tx(tx, from_server=True)

        assert success is False
        assert not mempool.contains(h)

    def test_coinbase_rejected(self) -> None:
        params = ConsensusParams.default()
        service, mempool, _utxo, _net = _make_service()
        data = encode_coinbase_data(
            CoinbaseData(
                height=1, outputs=(CoinbaseOutput(recipient_pubkey=b"LibNaCLPK:m", amount=1),)
            )
        )
        tx = Transaction(
            sender_key=b"LibNaCLPK:m",
            data=data,
            timestamp=params.genesis_timestamp,
            signature=b"sig",
        )

        success, h, _message = service.submit_tx(tx, from_server=True)

        assert success is False
        assert not mempool.contains(h)

    def test_valid_transfer_accepted_with_fee(self) -> None:
        params = ConsensusParams.default()
        service, mempool, utxo, net = _make_service()
        premine = params.build_genesis().premine_utxos[0]
        utxo._utxos[premine.outpoint] = premine

        amount_out = premine.amount - 7  # fee = 7
        data = encode_transfer_data(
            TransferData(
                inputs=(
                    TransferInput(
                        prev_txid=premine.outpoint.txid, output_index=premine.outpoint.index
                    ),
                ),
                outputs=(TransferOutput(recipient_pubkey=b"LibNaCLPK:dst", amount=amount_out),),
            )
        )
        tx = Transaction(
            sender_key=premine.recipient_pubkey,
            data=data,
            timestamp=params.genesis_timestamp,
            signature=b"sig",
        )

        success, h, message = service.submit_tx(tx, from_server=False)

        assert success is True
        assert message == "accepted"
        assert mempool.contains(h)
        assert net.gossiped == [tx]  # gossip fires even for non-server submissions

    def test_transfer_spending_missing_utxo_rejected(self) -> None:
        params = ConsensusParams.default()
        service, mempool, _utxo, _net = _make_service()
        data = encode_transfer_data(
            TransferData(
                inputs=(TransferInput(prev_txid=b"\x11" * 32, output_index=0),),
                outputs=(TransferOutput(recipient_pubkey=b"LibNaCLPK:dst", amount=1),),
            )
        )
        tx = Transaction(
            sender_key=b"LibNaCLPK:m",
            data=data,
            timestamp=params.genesis_timestamp,
            signature=b"sig",
        )

        success, h, _message = service.submit_tx(tx, from_server=False)

        assert success is False
        assert not mempool.contains(h)
