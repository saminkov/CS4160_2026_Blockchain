from __future__ import annotations

from typing import Any

from blockchain.adapters.payloads import ReadyPayload, RegisterResponsePayload
from blockchain.app.registration_service import RegistrationService
from blockchain.core.consensus_params import ConsensusParams


class _FakeNetwork:
    def __init__(self) -> None:
        self.handlers: dict[type, Any] = {}
        self.tasks: dict[str, Any] = {}
        self.broadcasts: list[Any] = []

    def register_handler(self, payload_cls: type, handler: Any) -> None:
        self.handlers[payload_cls] = handler

    def register_task(self, name: str, fn: Any, interval: float) -> None:
        self.tasks[name] = fn

    def broadcast_members(self, payload: Any) -> None:
        self.broadcasts.append(payload)

    def members_online(self) -> set[bytes]:
        return set()

    def send(self, peer: bytes, payload: Any) -> None:  # pragma: no cover - unused
        pass


class _FakeRegistration:
    def __init__(self, *, server_online: bool = True) -> None:
        self.handlers: dict[type, Any] = {}
        self.sent: list[Any] = []
        self._server_online = server_online

    def register_handler(self, payload_cls: type, handler: Any) -> None:
        self.handlers[payload_cls] = handler

    def send_to_server(self, payload: Any) -> None:
        self.sent.append(payload)

    def server_online(self) -> bool:
        return self._server_online


def _ready(params: ConsensusParams, params_hash: bytes) -> ReadyPayload:
    return ReadyPayload(params.group_id, params_hash)


def _make(
    *, is_registrar: bool, server_online: bool = True
) -> tuple[RegistrationService, _FakeNetwork, _FakeRegistration]:
    params = ConsensusParams.default()
    net = _FakeNetwork()
    reg = _FakeRegistration(server_online=server_online)
    svc = RegistrationService(params, net, reg, is_registrar)
    return svc, net, reg


class TestRegistrationService:
    def test_params_mismatch_refuses_to_register(self) -> None:
        svc, net, reg = _make(is_registrar=True)
        params = ConsensusParams.default()
        peers = [pk for pk in params.member_pubkeys][:2]

        # One peer reports a mismatching params hash.
        net.handlers[ReadyPayload](peers[0], _ready(params, b"wrong-hash"))
        # The other peer is fine.
        net.handlers[ReadyPayload](peers[1], _ready(params, params.params_hash()))

        # Even with two ready peers, the registrar must never register on mismatch.
        net.tasks["registrar_loop"]()
        assert reg.sent == []

    def test_registrar_registers_once_both_peers_ready(self) -> None:
        svc, net, reg = _make(is_registrar=True)
        params = ConsensusParams.default()
        good = params.params_hash()
        peers = [pk for pk in params.member_pubkeys][:2]

        net.tasks["registrar_loop"]()  # no peers yet
        assert reg.sent == []

        for peer in peers:
            net.handlers[ReadyPayload](peer, _ready(params, good))
        net.tasks["registrar_loop"]()
        assert len(reg.sent) == 1

    def test_registrar_waits_for_server_online(self) -> None:
        svc, net, reg = _make(is_registrar=True, server_online=False)
        params = ConsensusParams.default()
        good = params.params_hash()
        for peer in [pk for pk in params.member_pubkeys][:2]:
            net.handlers[ReadyPayload](peer, _ready(params, good))

        # Server offline: registrar keeps waiting, no send yet.
        net.tasks["registrar_loop"]()
        assert reg.sent == []

        # Server comes online: next tick sends exactly once.
        reg._server_online = True
        net.tasks["registrar_loop"]()
        assert len(reg.sent) == 1

    def test_non_registrar_never_sends(self) -> None:
        svc, net, reg = _make(is_registrar=False)
        assert "registrar_loop" not in net.tasks
        # A response handler is still wired so the node can observe results.
        assert RegisterResponsePayload in reg.handlers
