from __future__ import annotations

from pathlib import Path

import pytest
from ipv8.keyvault.crypto import default_eccrypto

from blockchain.core.consensus_params import ConsensusParams
from blockchain.node.config import load_config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_KEYS = _REPO_ROOT / "keys"
_MEMBER_KEYS = (_KEYS / "my_key.pem", _KEYS / "sofi_key.pem", _KEYS / "polly_key.pem")


def _pubkey_of(key_path: Path) -> bytes:
    key = default_eccrypto.key_from_private_bin(key_path.read_bytes())
    return default_eccrypto.key_to_bin(key.pub())


class TestConfigIdentity:
    def test_registrar_auto_derived_from_lexicographically_first_key(self) -> None:
        roster = ConsensusParams.default().member_pubkeys
        registrar_key = min(roster)

        registrar_count = 0
        for key_path in _MEMBER_KEYS:
            cfg = load_config(["--key", str(key_path)])
            expected = _pubkey_of(key_path) == registrar_key
            assert cfg.is_registrar is expected
            registrar_count += int(cfg.is_registrar)

        assert registrar_count == 1  # exactly one designated registrar

    def test_explicit_registrar_flag_overrides_for_non_first_key(self) -> None:
        roster = ConsensusParams.default().member_pubkeys
        non_first = next(key for key in _MEMBER_KEYS if _pubkey_of(key) != min(roster))
        cfg = load_config(["--key", str(non_first), "--registrar"])
        assert cfg.is_registrar is True

    def test_non_roster_key_aborts(self, tmp_path: Path) -> None:
        foreign = default_eccrypto.generate_key("curve25519")
        key_file = tmp_path / "foreign.pem"
        key_file.write_bytes(default_eccrypto.key_to_bin(foreign))

        with pytest.raises(ValueError, match="member roster"):
            load_config(["--key", str(key_file)])
