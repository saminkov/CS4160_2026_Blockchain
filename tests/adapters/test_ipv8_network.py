from __future__ import annotations

from pathlib import Path

import pytest

from blockchain.adapters.ipv8_network import (
    allow_blockchain_sender,
    allow_registration_sender,
    load_my_pubkey,
)
from blockchain.adapters.payloads import (
    BlockInvPayload,
    GetBlockPayload,
    GetChainHeightPayload,
    ReadyPayload,
    RegisterResponsePayload,
    SubmitTransactionPayload,
    TxGossipPayload,
)
from blockchain.core.consensus_params import (
    REGISTRATION_COMMUNITY_ID,
    SERVER_PUBKEY_HEX,
    ConsensusParams,
    community_id_from_group_id,
    load_server_pubkey,
)

_SERVER = b"LibNaCLPK:lab3-server"
_MEMBER_A = b"LibNaCLPK:alice"
_MEMBER_B = b"LibNaCLPK:bob"
_MEMBER_C = b"LibNaCLPK:carol"
_MEMBERS = frozenset({_MEMBER_A, _MEMBER_B, _MEMBER_C})


class TestSenderPolicies:
    def test_server_payloads_require_server_key(self) -> None:
        for payload_cls in (SubmitTransactionPayload, GetChainHeightPayload, GetBlockPayload):
            assert allow_blockchain_sender(
                payload_cls,
                _SERVER,
                server_pubkey=_SERVER,
                member_pubkeys=_MEMBERS,
            )
            assert not allow_blockchain_sender(
                payload_cls,
                _MEMBER_A,
                server_pubkey=_SERVER,
                member_pubkeys=_MEMBERS,
            )

    def test_member_payloads_require_member_key(self) -> None:
        for payload_cls in (BlockInvPayload, TxGossipPayload, ReadyPayload):
            assert allow_blockchain_sender(
                payload_cls,
                _MEMBER_B,
                server_pubkey=_SERVER,
                member_pubkeys=_MEMBERS,
            )
            assert not allow_blockchain_sender(
                payload_cls,
                _SERVER,
                server_pubkey=_SERVER,
                member_pubkeys=_MEMBERS,
            )

    def test_registration_response_requires_server(self) -> None:
        assert allow_registration_sender(
            RegisterResponsePayload,
            _SERVER,
            server_pubkey=_SERVER,
        )
        assert not allow_registration_sender(
            RegisterResponsePayload,
            _MEMBER_A,
            server_pubkey=_SERVER,
        )

    def test_unknown_payload_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown blockchain"):
            allow_blockchain_sender(object, _MEMBER_A, server_pubkey=_SERVER, member_pubkeys=_MEMBERS)


class TestCommunityIds:
    def test_blockchain_overlay_id_matches_params(self) -> None:
        params = ConsensusParams.default()
        assert params.community_id == community_id_from_group_id(params.group_id)

    def test_registration_community_id_is_fixed_assignment_value(self) -> None:
        assert REGISTRATION_COMMUNITY_ID == b"Lab3Blockchain2026PW"


@pytest.mark.skipif(
    not Path("keys/my_key.pem").exists(),
    reason="local member key required",
)
def test_load_my_pubkey_from_key_file() -> None:
    pubkey = load_my_pubkey("keys/my_key.pem")
    assert pubkey.startswith(b"LibNaCLPK:")
    assert pubkey in ConsensusParams.default().member_pubkeys


def test_load_server_pubkey_from_spec_constant() -> None:
    pubkey = load_server_pubkey()
    assert pubkey == bytes.fromhex(SERVER_PUBKEY_HEX)
    assert pubkey.startswith(b"LibNaCLPK:")
