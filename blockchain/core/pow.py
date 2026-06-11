from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable

_NONCE = struct.Struct(">Q")
_ABORT_CHECK_INTERVAL = 4096
_MINING_PREFIX_SIZE = 76
_HASH_SIZE = 32


def has_leading_zero_bits(digest: bytes, bits: int) -> bool:
    if len(digest) != _HASH_SIZE:
        raise ValueError(f"digest must be {_HASH_SIZE} bytes, got {len(digest)}")
    if bits < 0 or bits > 256:
        raise ValueError("bits must be in [0, 256]")
    if bits == 0:
        return True
    return int.from_bytes(digest, "big") < (1 << (256 - bits))


def search_nonce(
    prefix76: bytes,
    difficulty: int,
    should_abort: Callable[[], bool],
) -> int | None:
    if len(prefix76) != _MINING_PREFIX_SIZE:
        raise ValueError(f"prefix must be {_MINING_PREFIX_SIZE} bytes, got {len(prefix76)}")
    if difficulty < 0 or difficulty > 256:
        raise ValueError("difficulty must be in [0, 256]")

    buf = bytearray(prefix76 + b"\x00" * 8)
    nonce_offset = _MINING_PREFIX_SIZE
    nonce = 0

    while True:
        if nonce % _ABORT_CHECK_INTERVAL == 0 and should_abort():
            return None
        _NONCE.pack_into(buf, nonce_offset, nonce)
        digest = hashlib.sha256(buf).digest()
        if has_leading_zero_bits(digest, difficulty):
            return nonce
        nonce += 1
