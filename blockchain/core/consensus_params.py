from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from blockchain.core.codec import CoinbaseData, CoinbaseOutput, encode_coinbase_data
from blockchain.core.entities import (
    HASH_SIZE,
    Block,
    BlockHeader,
    Outpoint,
    Transaction,
    UTXO,
)
from blockchain.core.hashing import block_hash, tx_hash, txs_hash

COIN = 100_000_000
PREMINE_TOTAL_COINS = 21_000_000
PREMINE_PER_MEMBER_COINS = 7_000_000
INITIAL_REWARD_COINS = 50
HALVING_INTERVAL = 10
COINBASE_MATURITY = 3
TIMESTAMP_TOLERANCE_SECONDS = 2 * 60 * 60
DIFFICULTY_BITS = 12
GENESIS_TIMESTAMP = 1_231_006_505
GENESIS_NONCE = 2_083_236_893
COMMUNITY_ID_PREFIX = b"Lab3"
REGISTRATION_COMMUNITY_ID = b"Lab3Blockchain2026PW"
GENESIS_HEADLINE = (
    b"The Times 03/Jan/2009 Chancellor on brink of second bailout for banks."
)
GENESIS_COINBASE_SIGNATURE = b"GENESIS"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_KEYS_DIR = _REPO_ROOT / "keys"
_MEMBER_PUBKEY_FILES = (
    _KEYS_DIR / "my_key.pem.pub",
    _KEYS_DIR / "sofi_key.pem.pub",
    _KEYS_DIR / "polly_key.pem.pub",
)
_GROUP_REGISTRATION_FILE = _KEYS_DIR / "group_registration.json"
_SERVER_PUBKEY_FILE = _KEYS_DIR / "server_key.pem.pub"


@lru_cache(maxsize=1)
def load_group_id() -> str:
    payload = json.loads(_GROUP_REGISTRATION_FILE.read_text(encoding="utf-8"))
    group_id = payload.get("group_id")
    return group_id


def community_id_from_group_id(group_id: str) -> bytes:
    return COMMUNITY_ID_PREFIX + group_id.encode("ascii")


@lru_cache(maxsize=1)
def load_member_pubkeys() -> tuple[bytes, ...]:
    pubkeys = tuple(sorted(path.read_bytes() for path in _MEMBER_PUBKEY_FILES))
    return pubkeys


@lru_cache(maxsize=1)
def load_server_pubkey() -> bytes:
    pubkey = _SERVER_PUBKEY_FILE.read_bytes()
    if not pubkey:
        raise ValueError("server public key must be non-empty")
    return pubkey


@dataclass(frozen=True, slots=True)
class GenesisBuild:

    block: Block
    block_hash: bytes
    premine_utxos: tuple[UTXO, ...]


@dataclass(frozen=True, slots=True)
class ConsensusParams:

    difficulty_bits: int
    coin: int
    premine_total_coins: int
    premine_per_member_coins: int
    initial_reward_coins: int
    halving_interval: int
    coinbase_maturity: int
    timestamp_tolerance_seconds: int
    genesis_timestamp: int
    genesis_nonce: int
    genesis_headline: bytes
    genesis_coinbase_signature: bytes
    group_id: str
    community_id: bytes
    member_pubkeys: tuple[bytes, ...]

    def __post_init__(self) -> None:
        if self.difficulty_bits < 0 or self.difficulty_bits > 32:
            raise ValueError("difficulty_bits must fit uint32 semantics")
        if self.coin <= 0:
            raise ValueError("coin must be positive")
        if self.halving_interval <= 0:
            raise ValueError("halving_interval must be positive")
        if len(self.community_id) == 0:
            raise ValueError("community_id must be non-empty")
        if len(self.member_pubkeys) != 3:
            raise ValueError("member_pubkeys must contain exactly three keys")
        if tuple(sorted(self.member_pubkeys)) != self.member_pubkeys:
            raise ValueError("member_pubkeys must be sorted for canonical params")

    @classmethod
    def default(cls) -> ConsensusParams:
        group_id = load_group_id()
        return cls(
            difficulty_bits=DIFFICULTY_BITS,
            coin=COIN,
            premine_total_coins=PREMINE_TOTAL_COINS,
            premine_per_member_coins=PREMINE_PER_MEMBER_COINS,
            initial_reward_coins=INITIAL_REWARD_COINS,
            halving_interval=HALVING_INTERVAL,
            coinbase_maturity=COINBASE_MATURITY,
            timestamp_tolerance_seconds=TIMESTAMP_TOLERANCE_SECONDS,
            genesis_timestamp=GENESIS_TIMESTAMP,
            genesis_nonce=GENESIS_NONCE,
            genesis_headline=GENESIS_HEADLINE,
            genesis_coinbase_signature=GENESIS_COINBASE_SIGNATURE,
            group_id=group_id,
            community_id=community_id_from_group_id(group_id),
            member_pubkeys=load_member_pubkeys(),
        )

    def reward(self, height: int) -> int:
        """Block subsidy in base units at ``height`` (0 before the first mined block)."""
        if height < 1:
            return 0
        reward_coins = self.initial_reward_coins >> (height // self.halving_interval)
        return reward_coins * self.coin

    def premine_amount_base_units(self) -> int:
        return self.premine_per_member_coins * self.coin

    def build_genesis(self) -> GenesisBuild:
        amount = self.premine_amount_base_units()
        coinbase_body = encode_coinbase_data(
            CoinbaseData(
                height=0,
                outputs=tuple(
                    CoinbaseOutput(recipient_pubkey=pubkey, amount=amount)
                    for pubkey in self.member_pubkeys
                ),
            )
        )
        coinbase_tx = Transaction(
            sender_key=self.member_pubkeys[0],
            data=coinbase_body + self.genesis_headline,
            timestamp=self.genesis_timestamp,
            signature=self.genesis_coinbase_signature,
        )
        header = BlockHeader(
            prev_hash=b"\x00" * HASH_SIZE,
            txs_hash=txs_hash((coinbase_tx,)),
            timestamp=self.genesis_timestamp,
            difficulty=self.difficulty_bits,
            nonce=self.genesis_nonce,
        )
        block = Block(header=header, transactions=(coinbase_tx,))
        digest = block_hash(header)
        genesis_tx_hash = tx_hash(coinbase_tx)
        premine_utxos = tuple(
            UTXO(
                outpoint=Outpoint(txid=genesis_tx_hash, index=index),
                recipient_pubkey=pubkey,
                amount=amount,
                height_created=0,
                is_coinbase=True,
            )
            for index, pubkey in enumerate(self.member_pubkeys)
        )
        return GenesisBuild(block=block, block_hash=digest, premine_utxos=premine_utxos)

    def params_hash(self) -> bytes:
        return hashlib.sha256(self._canonical_bytes()).digest()

    def _canonical_bytes(self) -> bytes:
        parts: list[bytes] = [
            struct.pack(">I", self.difficulty_bits),
            struct.pack(">Q", self.coin),
            struct.pack(">Q", self.premine_total_coins),
            struct.pack(">Q", self.premine_per_member_coins),
            struct.pack(">Q", self.initial_reward_coins),
            struct.pack(">I", self.halving_interval),
            struct.pack(">I", self.coinbase_maturity),
            struct.pack(">I", self.timestamp_tolerance_seconds),
            struct.pack(">Q", self.genesis_timestamp),
            struct.pack(">Q", self.genesis_nonce),
            _length_prefixed(self.group_id.encode("utf-8")),
            _length_prefixed(self.community_id),
            _length_prefixed(self.genesis_headline),
            _length_prefixed(self.genesis_coinbase_signature),
        ]
        parts.extend(_length_prefixed(pubkey) for pubkey in self.member_pubkeys)
        return b"".join(parts)


def _length_prefixed(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data
