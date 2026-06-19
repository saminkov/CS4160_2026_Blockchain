from __future__ import annotations

import dataclasses
from typing import Any

from blockchain.adapters.block_store import InMemoryBlockStore
from blockchain.adapters.mempool import InMemoryMempool
from blockchain.adapters.payloads import BlockInvPayload
from blockchain.adapters.system_clock import FakeClock
from blockchain.adapters.utxo_store import InMemoryUTXOStore
from blockchain.app.chain_service import ChainService
from blockchain.app.mining_service import MiningService
from blockchain.app.validation_service import ValidationService
from blockchain.core.codec import (
    CoinbaseData,
    CoinbaseOutput,
    TransferData,
    TransferInput,
    TransferOutput,
    decode_coinbase_data,
    encode_coinbase_data,
    encode_transfer_data,
)
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import Block, BlockHeader, Transaction
from blockchain.core.hashing import header_mining_prefix, txs_hash
from blockchain.core.pow import search_nonce

# Low difficulty keeps the single PoW search in the gossip test instant.
_PARAMS = dataclasses.replace(ConsensusParams.default(), difficulty_bits=8)
_PK = b"LibNaCLPK:miner"


class _FakeCrypto:
    verify = staticmethod(lambda _pk, _msg, _sig: True)
    sign = staticmethod(lambda _priv, _msg: b"sig")


class _FakeMiner:
    """Records every mine() call (incl. the on_found callback) without searching."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.cancels = 0
        self.shutdowns = 0

    def mine(self, header_prefix: bytes, difficulty: int, generation: int, on_found: Any) -> None:
        self.calls.append(
            {
                "prefix": header_prefix,
                "difficulty": difficulty,
                "generation": generation,
                "on_found": on_found,
            }
        )

    def cancel(self) -> None:
        self.cancels += 1

    def shutdown(self) -> None:
        self.shutdowns += 1


class _FakeNetwork:
    def __init__(self) -> None:
        self.broadcasts: list[Any] = []

    def register_handler(self, payload_cls: type, handler: Any) -> None:  # pragma: no cover
        pass

    def register_task(self, name: str, fn: Any, interval: float) -> None:  # pragma: no cover
        pass

    def send(self, peer: bytes, payload: Any) -> None:  # pragma: no cover - unused
        pass

    def broadcast_members(self, payload: Any) -> None:
        self.broadcasts.append(payload)

    def members_online(self) -> set[bytes]:  # pragma: no cover - unused
        return set()


def _coinbase(height: int, *, timestamp: int) -> Transaction:
    data = encode_coinbase_data(
        CoinbaseData(
            height=height,
            outputs=(CoinbaseOutput(recipient_pubkey=_PK, amount=_PARAMS.reward(height)),),
        )
    )
    return Transaction(sender_key=_PK, data=data, timestamp=timestamp, signature=b"sig")


def _mine_block(prev_hash: bytes, height: int, *, timestamp: int) -> Block:
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


def _fee_transfer(utxo: Any, *, fee: int, timestamp: int) -> Transaction:
    """A transfer spending ``utxo`` that leaves ``fee`` as the input/output gap."""
    data = encode_transfer_data(
        TransferData(
            inputs=(TransferInput(prev_txid=utxo.outpoint.txid, output_index=utxo.outpoint.index),),
            outputs=(
                TransferOutput(recipient_pubkey=utxo.recipient_pubkey, amount=utxo.amount - fee),
            ),
        )
    )
    return Transaction(
        sender_key=utxo.recipient_pubkey, data=data, timestamp=timestamp, signature=b"sig"
    )


class _Harness:
    def __init__(self) -> None:
        self.miner = _FakeMiner()
        self.net = _FakeNetwork()
        self.utxo = InMemoryUTXOStore()
        self.mempool = InMemoryMempool()
        utxo = self.utxo
        forward: dict[str, Any] = {}
        self.chain = ChainService(
            _PARAMS,
            InMemoryBlockStore(),
            utxo,
            InMemoryMempool(),
            ValidationService(_PARAMS, None, utxo, _FakeCrypto()),
            FakeClock(_PARAMS.genesis_timestamp + 100),
            on_tip_changed=lambda tip: forward["cb"](tip),
        )
        self.mining = MiningService(
            _PARAMS,
            self.chain,
            self.mempool,
            utxo,
            self.miner,
            self.net,
            FakeClock(_PARAMS.genesis_timestamp + 100),
            _FakeCrypto(),
            _PK,
            b"priv",
        )
        forward["cb"] = self.mining.on_tip_changed
        self.chain.initialize_genesis()  # fires the first tip change → first mine() at gen 1


class TestMiningService:
    def test_tip_change_bumps_generation_and_remines(self) -> None:
        h = _Harness()
        assert len(h.miner.calls) == 1
        assert h.miner.calls[0]["generation"] == 1

        genesis = _PARAMS.build_genesis()
        block1 = _mine_block(genesis.block_hash, 1, timestamp=_PARAMS.genesis_timestamp + 100)
        assert h.chain.connect_block(block1, source="test").ok

        assert len(h.miner.calls) == 2
        assert h.miner.calls[1]["generation"] == 2

    def test_coinbase_pays_reward_plus_transfer_fees(self) -> None:
        h = _Harness()
        premine = _PARAMS.build_genesis().premine_utxos[0]
        fee = 7
        transfer = _fee_transfer(premine, fee=fee, timestamp=_PARAMS.genesis_timestamp + 150)
        h.mempool.add(transfer, 0)

        # Advance the tip so a fresh candidate selects the fee-bearing transfer.
        block1 = _mine_block(
            _PARAMS.build_genesis().block_hash, 1, timestamp=_PARAMS.genesis_timestamp + 100
        )
        assert h.chain.connect_block(block1, source="test").ok

        candidate = h.mining._candidate
        assert candidate is not None
        assert transfer in candidate.transactions
        coinbase = decode_coinbase_data(candidate.transactions[0].data)
        assert sum(o.amount for o in coinbase.outputs) == _PARAMS.reward(2) + fee

    def test_stale_on_found_is_ignored(self) -> None:
        h = _Harness()
        stale_on_found = h.miner.calls[0]["on_found"]  # generation 1

        genesis = _PARAMS.build_genesis()
        block1 = _mine_block(genesis.block_hash, 1, timestamp=_PARAMS.genesis_timestamp + 100)
        h.chain.connect_block(block1, source="test")  # bumps generation to 2

        stale_on_found(12345, 1)  # a result tagged with the old generation

        assert h.chain.height() == 1  # the stale result connected nothing
        assert not h.net.broadcasts

    def test_current_on_found_connects_and_gossips(self) -> None:
        h = _Harness()
        call = h.miner.calls[0]  # the height-1 candidate, generation 1
        nonce = search_nonce(call["prefix"], _PARAMS.difficulty_bits, should_abort=lambda: False)
        assert nonce is not None

        call["on_found"](nonce, call["generation"])

        assert h.chain.height() == 1
        invs = [p for p in h.net.broadcasts if isinstance(p, BlockInvPayload)]
        assert invs and invs[-1].height == 1
