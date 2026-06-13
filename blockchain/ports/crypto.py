from __future__ import annotations

from typing import Any, Protocol


class CryptoPort(Protocol):
    """Verify and produce signatures, tolerant of unsupported foreign keys."""

    def verify(self, pubkey_bin: bytes, msg: bytes, sig: bytes) -> bool:
        """Return True iff ``sig`` is ``msg`` signed by ``pubkey_bin``.

        Never raises on malformed or unsupported keys; returns False instead.
        """
        ...

    def sign(self, privkey: bytes, msg: bytes) -> bytes:
        """Sign ``msg`` with the binary private key and return the signature."""
        ...

    def pubkey_from_bin(self, raw: bytes) -> Any:
        """Reconstruct a public-key object from its binary serialization."""
        ...
