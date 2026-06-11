from __future__ import annotations

import logging

import pytest
from ipv8.keyvault.crypto import default_eccrypto

from blockchain.adapters.ecc_crypto import ECCryptoAdapter
from blockchain.ports.crypto import CryptoPort

_c: CryptoPort = ECCryptoAdapter()

# Stefan's key
_REAL_MEMBER_PUBKEY = bytes.fromhex(
    "4c69624e61434c504b3a45baf021e41a40b4b014a44b45398032a20d3d0a8a42caf2b25fbb114e5f3"
    "95086762c5f52d0914c1a98ae541461066463d90d133bb99bb0cad086c0152243b2"
)

_MSG = b"sender_key||data||timestamp_be"


@pytest.fixture
def keypair() -> tuple[bytes, bytes]:
    """Return (private_bin, public_bin) for a fresh curve25519 key."""
    key = default_eccrypto.generate_key("curve25519")
    return default_eccrypto.key_to_bin(key), default_eccrypto.key_to_bin(key.pub())


@pytest.fixture
def adapter() -> ECCryptoAdapter:
    return ECCryptoAdapter()


class TestRoundTripAndInterop:
    def test_sign_then_verify_roundtrip(
        self, adapter: ECCryptoAdapter, keypair: tuple[bytes, bytes]
    ) -> None:
        priv, pub = keypair
        sig = adapter.sign(priv, _MSG)
        assert adapter.verify(pub, _MSG, sig) is True

    def test_verify_accepts_signature_from_raw_eccrypto(
        self, adapter: ECCryptoAdapter
    ) -> None:
        key = default_eccrypto.generate_key("curve25519")
        pub = default_eccrypto.key_to_bin(key.pub())
        sig = default_eccrypto.create_signature(key, _MSG)
        assert adapter.verify(pub, _MSG, sig) is True

    def test_raw_eccrypto_accepts_adapter_signature(
        self, adapter: ECCryptoAdapter
    ) -> None:
        key = default_eccrypto.generate_key("curve25519")
        priv = default_eccrypto.key_to_bin(key)
        sig = adapter.sign(priv, _MSG)
        assert default_eccrypto.is_valid_signature(key, _MSG, sig)


class TestVerifyRejections:
    def test_rejects_tampered_message(
        self, adapter: ECCryptoAdapter, keypair: tuple[bytes, bytes]
    ) -> None:
        priv, pub = keypair
        sig = adapter.sign(priv, _MSG)
        assert adapter.verify(pub, _MSG + b"x", sig) is False

    def test_rejects_tampered_signature(
        self, adapter: ECCryptoAdapter, keypair: tuple[bytes, bytes]
    ) -> None:
        priv, pub = keypair
        sig = bytearray(adapter.sign(priv, _MSG))
        sig[0] ^= 0xFF
        assert adapter.verify(pub, _MSG, bytes(sig)) is False

    def test_rejects_wrong_key(
        self, adapter: ECCryptoAdapter, keypair: tuple[bytes, bytes]
    ) -> None:
        priv, _ = keypair
        sig = adapter.sign(priv, _MSG)
        other_pub = default_eccrypto.key_to_bin(
            default_eccrypto.generate_key("curve25519").pub()
        )
        assert adapter.verify(other_pub, _MSG, sig) is False


class TestVerifyTolerance:
    def test_tolerates_garbage_pubkey(self, adapter: ECCryptoAdapter) -> None:
        assert adapter.verify(b"not-a-key", _MSG, b"sig") is False

    def test_tolerates_empty_inputs(self, adapter: ECCryptoAdapter) -> None:
        assert adapter.verify(b"", b"", b"") is False

    def test_tolerates_real_member_pubkey_with_bogus_sig(
        self, adapter: ECCryptoAdapter
    ) -> None:
        assert adapter.verify(_REAL_MEMBER_PUBKEY, _MSG, b"bogus-signature") is False

    def test_debug_log_on_reject(
        self, adapter: ECCryptoAdapter, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="blockchain.crypto"):
            adapter.verify(b"not-a-key", _MSG, b"sig")
        assert any(record.levelno == logging.DEBUG for record in caplog.records)


class TestKeyHandling:
    def test_pubkey_from_bin_roundtrips(
        self, adapter: ECCryptoAdapter, keypair: tuple[bytes, bytes]
    ) -> None:
        _, pub = keypair
        key = adapter.pubkey_from_bin(pub)
        assert default_eccrypto.key_to_bin(key) == pub

    def test_pubkey_from_bin_raises_on_garbage(self, adapter: ECCryptoAdapter) -> None:
        with pytest.raises(Exception):  # noqa: B017 — any failure is acceptable here
            adapter.pubkey_from_bin(b"not-a-key")

    def test_sign_raises_on_bad_privkey(self, adapter: ECCryptoAdapter) -> None:
        with pytest.raises(Exception):  # noqa: B017
            adapter.sign(b"not-a-key", _MSG)
