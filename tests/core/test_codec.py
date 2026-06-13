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

class TestVarlenHMCDC:
    def test_unpack_truncated_length_prefix(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            unpack_varlen_h(b"\x00")

    def test_unpack_truncated_payload(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            # declares 5 bytes, only 3 available
            unpack_varlen_h(b"\x00\x05abc")  


class TestTransactionMCDC:
    def test_unpack_tx_truncated_at_timestamp(self) -> None:
        tx = _tx()
        wire = pack_tx(tx)
        key_end = 2 + len(tx.sender_key)
        data_end = key_end + 2 + len(tx.data)
        # 3 of 8 timestamp bytes
        truncated = wire[: data_end + 3] 
        with pytest.raises(ValueError, match="truncated"):
            unpack_tx(truncated)


class TestTimestampSigningMCDC:
    def test_negative_timestamp_raises(self) -> None:
        with pytest.raises(ValueError):
            pack_timestamp_for_signing(-1)

    def test_overflow_timestamp_raises(self) -> None:
        with pytest.raises(ValueError):
            pack_timestamp_for_signing(0x1_0000_0000_0000_0000)


class TestTransferDataMCDC:
    def test_encode_rejects_wrong_txid_length(self) -> None:
        td = TransferData(
            inputs=(TransferInput(prev_txid=b"\x00" * 31, output_index=0),),
            outputs=(TransferOutput(recipient_pubkey=b"pk", amount=1),),
        )
        with pytest.raises(ValueError, match="32 bytes"):
            encode_transfer_data(td)

    def test_encode_rejects_empty_output_pubkey(self) -> None:
        td = TransferData(
            inputs=(TransferInput(prev_txid=b"\x00" * 32, output_index=0),),
            outputs=(TransferOutput(recipient_pubkey=b"", amount=1),),
        )
        with pytest.raises(ValueError, match="pubkey"):
            encode_transfer_data(td)

    def test_decode_rejects_wrong_magic(self) -> None:
        with pytest.raises(ValueError, match="UTX1"):
            decode_transfer_data(b"CBAS" + b"\x00" * 10)

    def test_decode_truncated_input(self) -> None:
        # 1 input declared but only 10 bytes supplied (need 34: 32 txid + 2 index)
        with pytest.raises(ValueError, match="truncated"):
            decode_transfer_data(b"UTX1" + b"\x00\x01" + b"\x00" * 10)


class TestCoinbaseDataMCDC:
    def test_encode_rejects_empty_pubkey(self) -> None:
        cd = CoinbaseData(
            height=1,
            outputs=(CoinbaseOutput(recipient_pubkey=b"", amount=1),),
        )
        with pytest.raises(ValueError, match="pubkey"):
            encode_coinbase_data(cd)

    def test_decode_rejects_wrong_magic(self) -> None:
        with pytest.raises(ValueError, match="CBAS"):
            decode_coinbase_data(b"UTX1" + b"\x00" * 10)

    def test_decode_truncated_header(self) -> None:
        # Only 5 bytes after CBAS magic; need 10 (8 height + 2 count)
        with pytest.raises(ValueError, match="truncated"):
            decode_coinbase_data(b"CBAS" + b"\x00" * 5)

    def test_decode_truncated_output(self) -> None:
        # Valid magic + height + count=1 but zero output bytes
        data = b"CBAS" + struct.pack(">Q", 1) + struct.pack(">H", 1)
        with pytest.raises(ValueError, match="truncated"):
            decode_coinbase_data(data)


class TestBlockBodyMCDC:
    def test_unpack_truncated_count(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            # 2 bytes, need 4
            unpack_block_body(b"\x00\x01")


class TestChunkingMCDC:
    def test_rejects_zero_chunk_size(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            chunk_block_body(b"data", max_chunk_size=0)

    def test_rejects_negative_chunk_size(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            chunk_block_body(b"data", max_chunk_size=-1)
