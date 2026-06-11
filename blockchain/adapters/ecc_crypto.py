from __future__ import annotations

from typing import Any

from ipv8.keyvault.crypto import default_eccrypto

from blockchain.logging_setup import get_logger
from blockchain.ports.crypto import CryptoPort

_log = get_logger("crypto")


class ECCryptoAdapter(CryptoPort):
    """CryptoPort backed by IPv8's default_eccrypto"""

    def verify(self, pubkey_bin: bytes, msg: bytes, sig: bytes) -> bool:
        try:
            key = default_eccrypto.key_from_public_bin(pubkey_bin)
            return bool(default_eccrypto.is_valid_signature(key, msg, sig))
        except Exception as exc:
            _log.debug("signature verification failed: %s", exc)
            return False

    def sign(self, privkey: bytes, msg: bytes) -> bytes:
        key = default_eccrypto.key_from_private_bin(privkey)
        return bytes(default_eccrypto.create_signature(key, msg))

    def pubkey_from_bin(self, raw: bytes) -> Any:
        return default_eccrypto.key_from_public_bin(raw)
