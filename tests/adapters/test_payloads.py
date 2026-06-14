from __future__ import annotations

import pytest
from ipv8.messaging.serialization import Serializer

from blockchain.adapters.payloads import (
    BlockDataPayload,
    BlockInvPayload,
    ChainHeightResponsePayload,
    GetBlockByHeightPayload,
    GetBlockDataPayload,
    GetChainHeightPayload,
    ReadyPayload,
    RegisterBlockchainPayload,
    RegisterResponsePayload,
    SubmitTransactionPayload,
    SubmitTransactionResponsePayload,
    TxGossipPayload,
    block_data_not_found,
    block_data_payloads_from_block,
    block_from_block_data_payloads,
    block_response_from_block,
    block_response_not_found,
    is_block_data_not_found,
    is_block_response_not_found,
    transaction_from_gossip,
    transaction_from_submit,
    tx_hashes_from_wire,
    tx_hashes_to_wire,
)
from blockchain.core.codec import DEFAULT_CHUNK_SIZE
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import HASH_SIZE, Transaction
from blockchain.core.hashing import block_hash, tx_hash

_SERIALIZER = Serializer()
_ZERO = b"\x00" * HASH_SIZE
_PUBKEY = b"LibNaCLPK:alice"


def _roundtrip(payload: object) -> None:
    packed = _SERIALIZER.pack_serializable(payload)  # type: ignore[arg-type]
    cls = type(payload)
    unpacked, offset = _SERIALIZER.unpack_serializable(cls, packed)  # type: ignore[arg-type]
    assert offset == len(packed)
    for name in cls.names:
        assert getattr(unpacked, name) == getattr(payload, name)


@pytest.mark.parametrize(
    "payload",
    [
        RegisterBlockchainPayload("group-1", b"Lab3community"),
        RegisterResponsePayload(True, "ok"),
        SubmitTransactionPayload(_PUBKEY, b"data", 1_700_000_000, b"sig"),
        SubmitTransactionResponsePayload(True, _ZERO, "accepted"),
        GetChainHeightPayload(7),
        ChainHeightResponsePayload(7, 12, _ZERO),
        BlockInvPayload(_ZERO, 3),
        GetBlockDataPayload(_ZERO),
        BlockDataPayload(1, _ZERO, _ZERO, 1_700_000_000, 12, 42, _ZERO, 0, 1, b"body"),
        TxGossipPayload(_PUBKEY, b"gossip", 1_700_000_001, b"sig"),
        ReadyPayload("group-1", _ZERO),
        GetBlockByHeightPayload(5),
    ],
)
def test_payload_wire_roundtrip(payload: object) -> None:
    _roundtrip(payload)


def test_block_response_and_block_data_helpers() -> None:
    genesis = ConsensusParams.default().build_genesis()
    block = genesis.block
    height = 0
    response = block_response_from_block(block, height)
    assert not is_block_response_not_found(response)
    assert tx_hashes_from_wire(response.tx_hashes) == (tx_hash(block.transactions[0]),)

    not_found = block_response_not_found(99)
    assert is_block_response_not_found(not_found)
    assert not_found.height == 99

    payloads = block_data_payloads_from_block(block, height, max_chunk_size=DEFAULT_CHUNK_SIZE)
    assert len(payloads) >= 1
    restored = block_from_block_data_payloads(payloads)
    assert restored == block
    assert block_hash(restored.header) == genesis.block_hash

    missing = block_data_not_found(42)
    assert is_block_data_not_found(missing)


def test_transaction_from_submit_and_gossip() -> None:
    tx = Transaction(sender_key=_PUBKEY, data=b"payload", timestamp=99, signature=b"sig")
    submit = SubmitTransactionPayload(tx.sender_key, tx.data, tx.timestamp, tx.signature)
    gossip = TxGossipPayload(tx.sender_key, tx.data, tx.timestamp, tx.signature)
    assert transaction_from_submit(submit) == tx
    assert transaction_from_gossip(gossip) == tx


def test_tx_hashes_wire_rejects_bad_length() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        tx_hashes_to_wire((b"\x00" * 31,))
