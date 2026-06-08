from __future__ import annotations

from dataclasses import dataclass

HEADER_SIZE = 84
HASH_SIZE = 32

MAGIC_COINBASE = b"CBAS"
MAGIC_UTXO_TRANSFER = b"UTX1"


def _check_hash(value: bytes, name: str) -> None:
    if len(value) != HASH_SIZE:
        raise ValueError(f"{name} must be {HASH_SIZE} bytes, got {len(value)}")


def _check_uint32(value: int, name: str) -> None:
    if value < 0 or value > 0xFFFFFFFF:
        raise ValueError(f"{name} must fit uint32")


def _check_uint64(value: int, name: str) -> None:
    if value < 0 or value > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"{name} must fit uint64")


def _check_uint16(value: int, name: str) -> None:
    if value < 0 or value > 0xFFFF:
        raise ValueError(f"{name} must fit uint16")


@dataclass(frozen=True, slots=True)
class BlockHeader:

    prev_hash: bytes
    txs_hash: bytes
    timestamp: int
    difficulty: int
    nonce: int

    def __post_init__(self) -> None:
        _check_hash(self.prev_hash, "prev_hash")
        _check_hash(self.txs_hash, "txs_hash")
        _check_uint64(self.timestamp, "timestamp")
        _check_uint32(self.difficulty, "difficulty")
        _check_uint64(self.nonce, "nonce")


@dataclass(frozen=True, slots=True)
class Transaction:

    sender_key: bytes
    data: bytes
    timestamp: int
    signature: bytes

    def __post_init__(self) -> None:
        if not self.sender_key:
            raise ValueError("sender_key must be non-empty")
        _check_uint64(self.timestamp, "timestamp")


@dataclass(frozen=True, slots=True)
class Block:

    header: BlockHeader
    transactions: tuple[Transaction, ...]


@dataclass(frozen=True, slots=True)
class Outpoint:

    txid: bytes
    index: int

    def __post_init__(self) -> None:
        _check_hash(self.txid, "txid")
        _check_uint16(self.index, "index")


@dataclass(frozen=True, slots=True)
class UTXO:

    outpoint: Outpoint
    recipient_pubkey: bytes
    amount: int
    height_created: int
    is_coinbase: bool

    def __post_init__(self) -> None:
        if not self.recipient_pubkey:
            raise ValueError("recipient_pubkey must be non-empty")
        _check_uint64(self.amount, "amount")
        if self.height_created < 0:
            raise ValueError("height_created must be non-negative")


@dataclass(frozen=True, slots=True)
class BlockNode:

    block: Block
    block_hash: bytes
    height: int
    parent_hash: bytes

    def __post_init__(self) -> None:
        _check_hash(self.block_hash, "block_hash")
        _check_hash(self.parent_hash, "parent_hash")
        if self.height < 0:
            raise ValueError("height must be non-negative")


@dataclass(frozen=True, slots=True)
class UndoRecord:

    spent: tuple[UTXO, ...]
    created: tuple[Outpoint, ...]
