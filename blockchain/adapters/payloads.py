from __future__ import annotations

from collections.abc import Sequence

from ipv8.messaging.payload_dataclass import VariablePayload

from blockchain.core.codec import (
    DEFAULT_CHUNK_SIZE,
    chunk_block_body,
    pack_block_body,
    unchunk_block_body,
    unpack_block_body,
)
from blockchain.core.entities import HASH_SIZE, Block, BlockHeader, Transaction
from blockchain.core.hashing import block_hash, tx_hash

# --- Registration community (server-facing, msg_id 1–2) ---


class RegisterBlockchainPayload(VariablePayload):
    msg_id = 1
    format_list = ["varlenHutf8", "varlenH"]
    names = ["group_id", "community_id"]


class RegisterResponsePayload(VariablePayload):
    msg_id = 2
    format_list = ["?", "varlenHutf8"]
    names = ["success", "message"]


# --- Blockchain community — server-facing (msg_id 1–6) ---


class SubmitTransactionPayload(VariablePayload):
    msg_id = 1
    format_list = ["varlenH", "varlenH", "q", "varlenH"]
    names = ["sender_key", "data", "timestamp", "signature"]


class SubmitTransactionResponsePayload(VariablePayload):
    msg_id = 2
    format_list = ["?", "varlenH", "varlenHutf8"]
    names = ["success", "tx_hash", "message"]


class GetChainHeightPayload(VariablePayload):
    msg_id = 3
    format_list = ["q"]
    names = ["request_id"]


class ChainHeightResponsePayload(VariablePayload):
    msg_id = 4
    format_list = ["q", "q", "varlenH"]
    names = ["request_id", "height", "tip_hash"]


class GetBlockPayload(VariablePayload):
    msg_id = 5
    format_list = ["q"]
    names = ["height"]


class BlockResponsePayload(VariablePayload):
    msg_id = 6
    format_list = ["q", "varlenH", "varlenH", "q", "q", "q", "varlenH", "varlenH"]
    names = [
        "height",
        "prev_hash",
        "txs_hash",
        "timestamp",
        "difficulty",
        "nonce",
        "block_hash",
        "tx_hashes",
    ]


# --- Blockchain community — inter-node (msg_id 7–12) ---


class BlockInvPayload(VariablePayload):
    msg_id = 7
    format_list = ["varlenH", "q"]
    names = ["block_hash", "height"]


class GetBlockDataPayload(VariablePayload):
    msg_id = 8
    format_list = ["varlenH"]
    names = ["block_hash"]


class BlockDataPayload(VariablePayload):
    msg_id = 9
    format_list = ["q", "varlenH", "varlenH", "q", "q", "q", "varlenH", "q", "q", "varlenH"]
    names = [
        "height",
        "prev_hash",
        "txs_hash",
        "timestamp",
        "difficulty",
        "nonce",
        "block_hash",
        "chunk_index",
        "chunk_count",
        "body",
    ]


class TxGossipPayload(VariablePayload):
    msg_id = 10
    format_list = ["varlenH", "varlenH", "q", "varlenH"]
    names = ["sender_key", "data", "timestamp", "signature"]


class ReadyPayload(VariablePayload):
    msg_id = 11
    format_list = ["varlenHutf8", "varlenH"]
    names = ["group_id", "params_hash"]


class GetBlockByHeightPayload(VariablePayload):
    msg_id = 12
    format_list = ["q"]
    names = ["height"]


def transaction_from_wire(
    *,
    sender_key: bytes,
    data: bytes,
    timestamp: int,
    signature: bytes,
) -> Transaction:
    return Transaction(
        sender_key=sender_key,
        data=data,
        timestamp=timestamp,
        signature=signature,
    )


def transaction_from_submit(payload: SubmitTransactionPayload) -> Transaction:
    return transaction_from_wire(
        sender_key=payload.sender_key,
        data=payload.data,
        timestamp=payload.timestamp,
        signature=payload.signature,
    )


def transaction_from_gossip(payload: TxGossipPayload) -> Transaction:
    return transaction_from_wire(
        sender_key=payload.sender_key,
        data=payload.data,
        timestamp=payload.timestamp,
        signature=payload.signature,
    )


def tx_hashes_to_wire(tx_hashes: Sequence[bytes]) -> bytes:
    digests = tuple(tx_hashes)
    for index, digest in enumerate(digests):
        if len(digest) != HASH_SIZE:
            raise ValueError(f"tx_hashes[{index}] must be {HASH_SIZE} bytes, got {len(digest)}")
    return b"".join(digests)


def tx_hashes_from_wire(data: bytes) -> tuple[bytes, ...]:
    if not data:
        return ()
    if len(data) % HASH_SIZE != 0:
        raise ValueError("tx_hashes wire data must be a multiple of 32 bytes")
    return tuple(data[index : index + HASH_SIZE] for index in range(0, len(data), HASH_SIZE))


def block_response_from_block(block: Block, height: int) -> BlockResponsePayload:
    header = block.header
    digest = block_hash(header)
    return BlockResponsePayload(
        height,
        header.prev_hash,
        header.txs_hash,
        header.timestamp,
        header.difficulty,
        header.nonce,
        digest,
        tx_hashes_to_wire(tuple(tx_hash(tx) for tx in block.transactions)),
    )


def block_response_not_found(height: int) -> BlockResponsePayload:
    empty = b""
    return BlockResponsePayload(height, empty, empty, 0, 0, 0, empty, empty)


def block_data_not_found(height: int) -> BlockDataPayload:
    empty = b""
    return BlockDataPayload(height, empty, empty, 0, 0, 0, empty, 0, 0, empty)


def block_data_payloads_from_block(
    block: Block,
    height: int,
    *,
    max_chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[BlockDataPayload, ...]:
    header = block.header
    digest = block_hash(header)
    body = pack_block_body(block.transactions)
    chunks = chunk_block_body(body, max_chunk_size)
    return tuple(
        BlockDataPayload(
            height,
            header.prev_hash,
            header.txs_hash,
            header.timestamp,
            header.difficulty,
            header.nonce,
            digest,
            chunk_index,
            len(chunks),
            chunk,
        )
        for chunk_index, chunk in enumerate(chunks)
    )


def block_from_block_data_payloads(payloads: Sequence[BlockDataPayload]) -> Block:
    if not payloads:
        raise ValueError("BlockData payload sequence must not be empty")
    ordered = sorted(payloads, key=lambda payload: payload.chunk_index)
    first = ordered[0]
    if first.chunk_count != len(ordered):
        raise ValueError("incomplete BlockData chunk sequence")
    for payload in ordered:
        if payload.height != first.height:
            raise ValueError("BlockData chunks disagree on height")
        if payload.block_hash != first.block_hash:
            raise ValueError("BlockData chunks disagree on block_hash")
        if payload.prev_hash != first.prev_hash:
            raise ValueError("BlockData chunks disagree on prev_hash")
        if payload.txs_hash != first.txs_hash:
            raise ValueError("BlockData chunks disagree on txs_hash")
        if payload.timestamp != first.timestamp:
            raise ValueError("BlockData chunks disagree on timestamp")
        if payload.difficulty != first.difficulty:
            raise ValueError("BlockData chunks disagree on difficulty")
        if payload.nonce != first.nonce:
            raise ValueError("BlockData chunks disagree on nonce")
        if payload.chunk_count != first.chunk_count:
            raise ValueError("BlockData chunks disagree on chunk_count")
        if payload.chunk_index < 0 or payload.chunk_index >= payload.chunk_count:
            raise ValueError("BlockData chunk_index out of range")

    header = BlockHeader(
        prev_hash=first.prev_hash,
        txs_hash=first.txs_hash,
        timestamp=first.timestamp,
        difficulty=first.difficulty,
        nonce=first.nonce,
    )
    if block_hash(header) != first.block_hash:
        raise ValueError("BlockData header fields do not match block_hash")
    body = unchunk_block_body(tuple(payload.body for payload in ordered))
    transactions = unpack_block_body(body)
    return Block(header=header, transactions=transactions)


def is_block_data_not_found(payload: BlockDataPayload) -> bool:
    return payload.block_hash == b"" and payload.body == b""


def is_block_response_not_found(payload: BlockResponsePayload) -> bool:
    return payload.block_hash == b"" and payload.tx_hashes == b""
