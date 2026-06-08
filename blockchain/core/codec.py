from __future__ import annotations

import struct
from dataclasses import dataclass

from blockchain.core.entities import (
    HASH_SIZE,
    HEADER_SIZE,
    MAGIC_COINBASE,
    MAGIC_UTXO_TRANSFER,
    Block,
    BlockHeader,
    Transaction,
)

_HEADER_STRUCT = struct.Struct(">32s32sQIQ")
_VARLEN_UINT16 = struct.Struct(">H")
_TX_TIMESTAMP = struct.Struct(">q")
_UINT16 = struct.Struct(">H")
_UINT32 = struct.Struct(">I")
_UINT64 = struct.Struct(">Q")
_SIGN_TIMESTAMP = struct.Struct(">Q")

DEFAULT_CHUNK_SIZE = 60_000


@dataclass(frozen=True, slots=True)
class TransferInput:
    prev_txid: bytes
    output_index: int


@dataclass(frozen=True, slots=True)
class TransferOutput:
    recipient_pubkey: bytes
    amount: int


@dataclass(frozen=True, slots=True)
class TransferData:
    inputs: tuple[TransferInput, ...]
    outputs: tuple[TransferOutput, ...]


@dataclass(frozen=True, slots=True)
class CoinbaseOutput:
    recipient_pubkey: bytes
    amount: int


@dataclass(frozen=True, slots=True)
class CoinbaseData:
    height: int
    outputs: tuple[CoinbaseOutput, ...]


def pack_varlen_h(data: bytes) -> bytes:
    if len(data) > 0xFFFF:
        raise ValueError(f"varlenH payload exceeds 65535 bytes: {len(data)}")
    return _VARLEN_UINT16.pack(len(data)) + data


def unpack_varlen_h(data: bytes, offset: int = 0) -> tuple[bytes, int]:
    if offset + 2 > len(data):
        raise ValueError("truncated varlenH length prefix")
    (length,) = _VARLEN_UINT16.unpack_from(data, offset)
    offset += 2
    end = offset + length
    if end > len(data):
        raise ValueError("truncated varlenH payload")
    return data[offset:end], end


def pack_timestamp_for_signing(timestamp: int) -> bytes:
    if timestamp < 0 or timestamp > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("timestamp must fit uint64 for signing")
    return _SIGN_TIMESTAMP.pack(timestamp)


def pack_header(header: BlockHeader) -> bytes:
    packed = _HEADER_STRUCT.pack(
        header.prev_hash,
        header.txs_hash,
        header.timestamp,
        header.difficulty,
        header.nonce,
    )
    if len(packed) != HEADER_SIZE:
        raise ValueError(f"packed header must be {HEADER_SIZE} bytes, got {len(packed)}")
    return packed


def unpack_header(data: bytes) -> BlockHeader:
    if len(data) != HEADER_SIZE:
        raise ValueError(f"header must be {HEADER_SIZE} bytes, got {len(data)}")
    prev_hash, txs_hash, timestamp, difficulty, nonce = _HEADER_STRUCT.unpack(data)
    return BlockHeader(
        prev_hash=prev_hash,
        txs_hash=txs_hash,
        timestamp=timestamp,
        difficulty=difficulty,
        nonce=nonce,
    )


def pack_tx(tx: Transaction) -> bytes:
    return (
        pack_varlen_h(tx.sender_key)
        + pack_varlen_h(tx.data)
        + _TX_TIMESTAMP.pack(tx.timestamp)
        + pack_varlen_h(tx.signature)
    )


def unpack_tx(data: bytes, offset: int = 0) -> tuple[Transaction, int]:
    """Parse a transaction from data starting at offset"""
    sender_key, offset = unpack_varlen_h(data, offset)
    payload, offset = unpack_varlen_h(data, offset)
    if offset + 8 > len(data):
        raise ValueError("truncated transaction timestamp")
    (timestamp,) = _TX_TIMESTAMP.unpack_from(data, offset)
    offset += 8
    signature, offset = unpack_varlen_h(data, offset)
    return (
        Transaction(
            sender_key=sender_key,
            data=payload,
            timestamp=timestamp,
            signature=signature,
        ),
        offset,
    )


def encode_transfer_data(transfer: TransferData) -> bytes:
    parts = [MAGIC_UTXO_TRANSFER, _UINT16.pack(len(transfer.inputs))]
    for inp in transfer.inputs:
        if len(inp.prev_txid) != HASH_SIZE:
            raise ValueError("transfer input prev_txid must be 32 bytes")
        parts.append(inp.prev_txid)
        parts.append(_UINT16.pack(inp.output_index))
    parts.append(_UINT16.pack(len(transfer.outputs)))
    for out in transfer.outputs:
        if not out.recipient_pubkey:
            raise ValueError("transfer output pubkey must be non-empty")
        if len(out.recipient_pubkey) > 0xFFFF:
            raise ValueError("transfer output pubkey exceeds uint16 length")
        parts.append(_UINT16.pack(len(out.recipient_pubkey)))
        parts.append(out.recipient_pubkey)
        parts.append(_UINT64.pack(out.amount))
    return b"".join(parts)


def decode_transfer_data(data: bytes) -> TransferData:
    if not data.startswith(MAGIC_UTXO_TRANSFER):
        raise ValueError("transfer data missing UTX1 magic")
    offset = len(MAGIC_UTXO_TRANSFER)
    inputs = _decode_transfer_inputs(data, offset)
    offset = inputs[1]
    outputs = _decode_transfer_outputs(data, offset)
    return TransferData(inputs=inputs[0], outputs=outputs[0])


def encode_coinbase_data(coinbase: CoinbaseData) -> bytes:
    parts = [MAGIC_COINBASE, _UINT64.pack(coinbase.height), _UINT16.pack(len(coinbase.outputs))]
    for out in coinbase.outputs:
        if not out.recipient_pubkey:
            raise ValueError("coinbase output pubkey must be non-empty")
        if len(out.recipient_pubkey) > 0xFFFF:
            raise ValueError("coinbase output pubkey exceeds uint16 length")
        parts.append(_UINT16.pack(len(out.recipient_pubkey)))
        parts.append(out.recipient_pubkey)
        parts.append(_UINT64.pack(out.amount))
    return b"".join(parts)


def decode_coinbase_data(data: bytes) -> CoinbaseData:
    if not data.startswith(MAGIC_COINBASE):
        raise ValueError("coinbase data missing CBAS magic")
    offset = len(MAGIC_COINBASE)
    if offset + 10 > len(data):
        raise ValueError("truncated coinbase header")
    height = _UINT64.unpack_from(data, offset)[0]
    offset += 8
    outputs = _decode_coinbase_outputs(data, offset)
    return CoinbaseData(height=height, outputs=outputs[0])


def pack_block_body(transactions: tuple[Transaction, ...]) -> bytes:
    if len(transactions) > 0xFFFFFFFF:
        raise ValueError("transaction count exceeds uint32")
    body = b"".join(pack_tx(tx) for tx in transactions)
    return _UINT32.pack(len(transactions)) + body


def unpack_block_body(data: bytes) -> tuple[Transaction, ...]:
    """Parse a length-prefixed block body into transactions"""
    if len(data) < 4:
        raise ValueError("truncated block body count")
    (count,) = _UINT32.unpack_from(data, 0)
    offset = 4
    txs: list[Transaction] = []
    for _ in range(count):
        tx, offset = unpack_tx(data, offset)
        txs.append(tx)
    if offset != len(data):
        raise ValueError("trailing bytes in block body")
    return tuple(txs)


def pack_block(block: Block) -> bytes:
    return pack_header(block.header) + pack_block_body(block.transactions)


def unpack_block(data: bytes) -> Block:
    if len(data) < HEADER_SIZE:
        raise ValueError("truncated block header")
    header = unpack_header(data[:HEADER_SIZE])
    transactions = unpack_block_body(data[HEADER_SIZE:])
    return Block(header=header, transactions=transactions)


def chunk_block_body(body: bytes, max_chunk_size: int = DEFAULT_CHUNK_SIZE) -> tuple[bytes, ...]:
    """Split a serialized block body for UDP-sized ``BlockData`` chunks."""
    if max_chunk_size <= 0:
        raise ValueError("max_chunk_size must be positive")
    if not body:
        return (b"",)
    return tuple(body[i : i + max_chunk_size] for i in range(0, len(body), max_chunk_size))


def unchunk_block_body(chunks: tuple[bytes, ...]) -> bytes:
    return b"".join(chunks)


def _decode_transfer_inputs(data: bytes, offset: int) -> tuple[tuple[TransferInput, ...], int]:
    if offset + 2 > len(data):
        raise ValueError("truncated transfer input count")
    (n_inputs,) = _UINT16.unpack_from(data, offset)
    offset += 2
    inputs: list[TransferInput] = []
    for _ in range(n_inputs):
        if offset + HASH_SIZE + 2 > len(data):
            raise ValueError("truncated transfer input")
        prev_txid = data[offset : offset + HASH_SIZE]
        offset += HASH_SIZE
        (output_index,) = _UINT16.unpack_from(data, offset)
        offset += 2
        inputs.append(TransferInput(prev_txid=prev_txid, output_index=output_index))
    return tuple(inputs), offset


def _decode_transfer_outputs(data: bytes, offset: int) -> tuple[tuple[TransferOutput, ...], int]:
    if offset + 2 > len(data):
        raise ValueError("truncated transfer output count")
    (n_outputs,) = _UINT16.unpack_from(data, offset)
    offset += 2
    outputs: list[TransferOutput] = []
    for _ in range(n_outputs):
        if offset + 2 > len(data):
            raise ValueError("truncated transfer output pubkey length")
        (pubkey_len,) = _UINT16.unpack_from(data, offset)
        offset += 2
        end = offset + pubkey_len
        if end + 8 > len(data):
            raise ValueError("truncated transfer output")
        recipient_pubkey = data[offset:end]
        offset = end
        (amount,) = _UINT64.unpack_from(data, offset)
        offset += 8
        outputs.append(TransferOutput(recipient_pubkey=recipient_pubkey, amount=amount))
    return tuple(outputs), offset


def _decode_coinbase_outputs(data: bytes, offset: int) -> tuple[tuple[CoinbaseOutput, ...], int]:
    if offset + 2 > len(data):
        raise ValueError("truncated coinbase output count")
    (n_outputs,) = _UINT16.unpack_from(data, offset)
    offset += 2
    outputs: list[CoinbaseOutput] = []
    for _ in range(n_outputs):
        if offset + 2 > len(data):
            raise ValueError("truncated coinbase output pubkey length")
        (pubkey_len,) = _UINT16.unpack_from(data, offset)
        offset += 2
        end = offset + pubkey_len
        if end + 8 > len(data):
            raise ValueError("truncated coinbase output")
        recipient_pubkey = data[offset:end]
        offset = end
        (amount,) = _UINT64.unpack_from(data, offset)
        offset += 8
        outputs.append(CoinbaseOutput(recipient_pubkey=recipient_pubkey, amount=amount))
    return tuple(outputs), offset
