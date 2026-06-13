"""Unit tests for ``blockchain.core.codec``."""

from __future__ import annotations

import struct

import pytest

from blockchain.core.codec import (
    DEFAULT_CHUNK_SIZE,
    CoinbaseData,
    CoinbaseOutput,
    TransferData,
    TransferInput,
    TransferOutput,
    chunk_block_body,
    decode_coinbase_data,
    decode_transfer_data,
    encode_coinbase_data,
    encode_transfer_data,
    pack_block,
    pack_block_body,
    pack_header,
    pack_timestamp_for_signing,
    pack_tx,
    pack_varlen_h,
    unchunk_block_body,
    unpack_block,
    unpack_block_body,
    unpack_header,
    unpack_tx,
    unpack_varlen_h,
)
from blockchain.core.entities import (
    HASH_SIZE,
    HEADER_SIZE,
    MAGIC_COINBASE,
    MAGIC_UTXO_TRANSFER,
    Block,
    BlockHeader,
    Transaction,
)

_ZERO_HASH = b"\x00" * HASH_SIZE
_PUBKEY_A = b"LibNaCLPK:alice"
_PUBKEY_B = b"LibNaCLPK:bob"


def _header(
    *,
    prev_hash: bytes = _ZERO_HASH,
    txs_hash: bytes = _ZERO_HASH,
    timestamp: int = 1_700_000_000,
    difficulty: int = 18,
    nonce: int = 42,
) -> BlockHeader:
    return BlockHeader(
        prev_hash=prev_hash,
        txs_hash=txs_hash,
        timestamp=timestamp,
        difficulty=difficulty,
        nonce=nonce,
    )


def _tx(
    *,
    sender_key: bytes = _PUBKEY_A,
    data: bytes = b"payload",
    timestamp: int = 1_700_000_000,
    signature: bytes = b"sig-bytes",
) -> Transaction:
    return Transaction(
        sender_key=sender_key,
        data=data,
        timestamp=timestamp,
        signature=signature,
    )


class TestVarlenH:
    def test_roundtrip(self) -> None:
        raw = b"test-key-bytes"
        payload, end = unpack_varlen_h(pack_varlen_h(raw))
        assert payload == raw
        assert end == 2 + len(raw)

    def test_rejects_oversized_payload(self) -> None:
        with pytest.raises(ValueError, match="65535"):
            pack_varlen_h(b"x" * 0x10000)


class TestHeader:
    def test_packed_size_is_84_bytes(self) -> None:
        assert len(pack_header(_header())) == HEADER_SIZE

    def test_roundtrip(self) -> None:
        header = _header(nonce=9_999_999_999)
        assert unpack_header(pack_header(header)) == header

    def test_rejects_wrong_length(self) -> None:
        with pytest.raises(ValueError, match="84"):
            unpack_header(b"\x00" * 80)


class TestTransaction:
    def test_varlen_h_and_q_wire_layout(self) -> None:
        tx = _tx()
        wire = pack_tx(tx)
        assert wire.startswith(struct.pack(">H", len(tx.sender_key)))
        restored, end = unpack_tx(wire)
        assert restored == tx
        assert end == len(wire)

    def test_signing_timestamp_bytes(self) -> None:
        ts = 1_700_000_000
        assert pack_timestamp_for_signing(ts) == struct.pack(">Q", ts)


class TestTransferData:
    def test_roundtrip(self) -> None:
        transfer = TransferData(
            inputs=(TransferInput(prev_txid=_ZERO_HASH, output_index=0),),
            outputs=(
                TransferOutput(recipient_pubkey=_PUBKEY_B, amount=500),
                TransferOutput(recipient_pubkey=_PUBKEY_A, amount=100),
            ),
        )
        encoded = encode_transfer_data(transfer)
        assert encoded.startswith(MAGIC_UTXO_TRANSFER)
        assert decode_transfer_data(encoded) == transfer


class TestCoinbaseData:
    def test_roundtrip(self) -> None:
        coinbase = CoinbaseData(
            height=7,
            outputs=(CoinbaseOutput(recipient_pubkey=_PUBKEY_A, amount=50_000_000_000),),
        )
        encoded = encode_coinbase_data(coinbase)
        assert encoded.startswith(MAGIC_COINBASE)
        assert decode_coinbase_data(encoded) == coinbase


class TestBlockBody:
    def test_empty_body(self) -> None:
        body = pack_block_body(())
        assert body == struct.pack(">I", 0)
        assert unpack_block_body(body) == ()

    def test_multiple_transactions(self) -> None:
        txs = (_tx(data=b"a"), _tx(data=b"b", sender_key=_PUBKEY_B))
        assert unpack_block_body(pack_block_body(txs)) == txs

    def test_rejects_trailing_bytes(self) -> None:
        body = pack_block_body((_tx(),)) + b"extra"
        with pytest.raises(ValueError, match="trailing"):
            unpack_block_body(body)


class TestBlock:
    def test_roundtrip(self) -> None:
        block = Block(header=_header(), transactions=(_tx(),))
        assert unpack_block(pack_block(block)) == block


class TestChunking:
    def test_split_and_join(self) -> None:
        body = pack_block_body(tuple(_tx(data=bytes([i])) for i in range(20)))
        chunks = chunk_block_body(body, max_chunk_size=64)
        assert len(chunks) > 1
        assert unchunk_block_body(chunks) == body

    def test_empty_body_chunk(self) -> None:
        assert chunk_block_body(b"") == (b"",)
        assert unchunk_block_body((b"",)) == b""

    def test_default_chunk_size_positive(self) -> None:
        assert DEFAULT_CHUNK_SIZE > 0
