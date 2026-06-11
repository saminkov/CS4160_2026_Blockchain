from __future__ import annotations

import hashlib
from collections.abc import Sequence

from blockchain.core.codec import pack_header, pack_timestamp_for_signing
from blockchain.core.entities import HASH_SIZE, BlockHeader, Transaction

_EMPTY_BODY_TXS_HASH = hashlib.sha256(b"").digest()


def tx_hash(tx: Transaction) -> bytes:
    payload = (
        tx.sender_key
        + tx.data
        + pack_timestamp_for_signing(tx.timestamp)
        + tx.signature
    )
    return hashlib.sha256(payload).digest()


def txs_hash(transactions: Sequence[Transaction]) -> bytes:
    if not transactions:
        return _EMPTY_BODY_TXS_HASH
    return hashlib.sha256(b"".join(tx_hash(tx) for tx in transactions)).digest()


def txs_hash_from_digests(tx_hashes: Sequence[bytes]) -> bytes:
    if not tx_hashes:
        return _EMPTY_BODY_TXS_HASH
    for index, digest in enumerate(tx_hashes):
        if len(digest) != HASH_SIZE:
            raise ValueError(f"tx_hashes[{index}] must be {HASH_SIZE} bytes, got {len(digest)}")
    return hashlib.sha256(b"".join(tx_hashes)).digest()


def block_hash(header: BlockHeader) -> bytes:
    return hashlib.sha256(pack_header(header)).digest()


def header_mining_prefix(header: BlockHeader) -> bytes:
    prefix = pack_header(header)[:76]
    if len(prefix) != 76:
        raise ValueError("mining prefix must be 76 bytes")
    return prefix
