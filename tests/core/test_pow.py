from __future__ import annotations

import hashlib
import struct

import pytest

from blockchain.core.pow import has_leading_zero_bits, search_nonce

_HASH_SIZE = 32


class TestHasLeadingZeroBits:
    def test_zero_bits_always_passes(self) -> None:
        assert has_leading_zero_bits(b"\xff" * _HASH_SIZE, 0) is True

    def test_all_zero_digest(self) -> None:
        assert has_leading_zero_bits(b"\x00" * _HASH_SIZE, 256) is True
        assert has_leading_zero_bits(b"\x00" * _HASH_SIZE, 255) is True

    def test_eight_leading_zero_bits(self) -> None:
        digest = b"\x00" + b"\x80" + b"\x00" * 30
        assert has_leading_zero_bits(digest, 8) is True
        assert has_leading_zero_bits(digest, 9) is False

    def test_rejects_bad_digest_length(self) -> None:
        with pytest.raises(ValueError, match="32 bytes"):
            has_leading_zero_bits(b"\x00" * 31, 1)

    def test_rejects_bits_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="bits must be"):
            has_leading_zero_bits(b"\x00" * _HASH_SIZE, 257)


class TestSearchNonce:
    def test_finds_nonce_for_low_difficulty(self) -> None:
        prefix = b"\x01" * 76
        nonce = search_nonce(prefix, difficulty=8, should_abort=lambda: False)
        assert nonce is not None
        digest = hashlib.sha256(prefix + struct.pack(">Q", nonce)).digest()
        assert has_leading_zero_bits(digest, 8) is True

    def test_returns_none_when_aborted_immediately(self) -> None:
        assert search_nonce(b"\x02" * 76, difficulty=8, should_abort=lambda: True) is None

    def test_rejects_bad_prefix_length(self) -> None:
        with pytest.raises(ValueError, match="76 bytes"):
            search_nonce(b"\x00" * 75, difficulty=1, should_abort=lambda: False)

class TestHasLeadingZeroBitsMCDC:
    """bits < 0 OR bits > 256: only > 256 was tested; add < 0."""

    def test_rejects_negative_bits(self) -> None:
        with pytest.raises(ValueError, match="bits must be"):
            has_leading_zero_bits(b"\x00" * _HASH_SIZE, -1)


class TestSearchNonceMCDC:
    """difficulty < 0 OR difficulty > 256: entirely untested."""

    def test_rejects_negative_difficulty(self) -> None:
        with pytest.raises(ValueError, match="difficulty"):
            search_nonce(b"\x00" * 76, difficulty=-1, should_abort=lambda: False)

    def test_rejects_difficulty_above_256(self) -> None:
        with pytest.raises(ValueError, match="difficulty"):
            search_nonce(b"\x00" * 76, difficulty=257, should_abort=lambda: False)


class TestSearchNonceCheckInterval:
    def test_rejects_check_interval_below_one(self) -> None:
        with pytest.raises(ValueError, match="check_interval"):
            search_nonce(b"\x00" * 76, difficulty=1, should_abort=lambda: False, check_interval=0)

    def test_abort_honoured_at_configured_cadence(self) -> None:
        # With check_interval=1, should_abort runs every nonce; abort on the 3rd call.
        calls = {"n": 0}

        def abort() -> bool:
            calls["n"] += 1
            return calls["n"] >= 3

        # High difficulty so it never finds a nonce before aborting.
        nonce = search_nonce(b"\x05" * 76, difficulty=64, should_abort=abort, check_interval=1)
        assert nonce is None
        assert calls["n"] == 3
