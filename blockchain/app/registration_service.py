from __future__ import annotations

import logging

from blockchain.adapters.payloads import (
    ReadyPayload,
    RegisterBlockchainPayload,
    RegisterResponsePayload,
)
from blockchain.core.consensus_params import ConsensusParams
from blockchain.ports.network import NetworkPort, RegistrationPort

logger = logging.getLogger(__name__)

BROADCAST_READY_INTERVAL = 10.0
REGISTER_CHECK_INTERVAL = 5.0
SERVER_RETRY_INTERVAL = 4 * 60.0  # 4 minutes


class RegistrationService:
    def __init__(
        self,
        params: ConsensusParams,
        network: NetworkPort,
        registration: RegistrationPort,
        is_registrar: bool,
    ) -> None:
        self._params = params
        self._network = network
        self._registration = registration
        self._is_registrar = is_registrar

        self._params_hash = self._params.params_hash()
        self._ready_peers: set[bytes] = set()
        self._registration_sent = False
        self._time_since_registration = 0.0
        self._params_mismatch = False

        self._network.register_handler(ReadyPayload, self._on_ready)
        self._registration.register_handler(
            RegisterResponsePayload, self._on_register_response
        )

        self._network.register_task(
            "broadcast_ready", self._broadcast_ready, BROADCAST_READY_INTERVAL
        )

        if self._is_registrar:
            self._network.register_task(
                "registrar_loop", self._registrar_loop, REGISTER_CHECK_INTERVAL
            )

    def _broadcast_ready(self) -> None:
        payload = ReadyPayload(self._params.group_id, self._params_hash)
        self._network.broadcast_members(payload)

    def _on_ready(self, peer: bytes, payload: ReadyPayload) -> None:
        if peer not in self._params.member_pubkeys:
            return

        if payload.params_hash != self._params_hash:
            self._params_mismatch = True
            logger.critical(
                "Consensus params mismatch with peer %s; refusing to register. "
                "This node will stay online for diagnosis but will NOT join the group.",
                peer.hex()[:8],
            )
            return

        if peer not in self._ready_peers:
            logger.info("Peer %s is ready", peer.hex()[:8])
            self._ready_peers.add(peer)

    def _registrar_loop(self) -> None:
        if self._params_mismatch:
            return
        if len(self._ready_peers) < 2:
            return

        if not self._registration_sent:
            # Keep retrying every tick until the server is actually reachable, so a
            # first send dropped because the server isn't online yet is retried in
            # seconds rather than waiting a full SERVER_RETRY_INTERVAL.
            if not self._registration.server_online():
                return
            self._send_registration()
            self._registration_sent = True
            self._time_since_registration = 0.0
        else:
            self._time_since_registration += REGISTER_CHECK_INTERVAL
            if self._time_since_registration >= SERVER_RETRY_INTERVAL:
                logger.info("Periodic re-registration to reset server retries...")
                self._send_registration()
                self._time_since_registration = 0.0

    def _send_registration(self) -> None:
        logger.info("Sending blockchain registration to server...")
        payload = RegisterBlockchainPayload(
            self._params.group_id,
            self._params.community_id,
        )
        self._registration.send_to_server(payload)

    def _on_register_response(
        self, peer: bytes, payload: RegisterResponsePayload
    ) -> None:
        if payload.success:
            logger.info("Registration successful: %s", payload.message)
        else:
            logger.error("Registration failed: %s", payload.message)
            if self._is_registrar:
                logger.info("Re-registering due to failure...")
                self._send_registration()
                self._time_since_registration = 0.0
