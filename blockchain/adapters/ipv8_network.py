from __future__ import annotations

import asyncio
import logging
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ipv8.community import Community, CommunitySettings
from ipv8.configuration import ConfigBuilder, Strategy, WalkerDefinition, default_bootstrap_defs
from ipv8.keyvault.crypto import default_eccrypto
from ipv8.lazy_community import lazy_wrapper
from ipv8.messaging.payload_dataclass import VariablePayload
from ipv8.peer import Peer
from ipv8_service import IPv8

from blockchain.adapters.payloads import (
    BlockDataPayload,
    BlockInvPayload,
    GetBlockByHeightPayload,
    GetBlockDataPayload,
    GetBlockPayload,
    GetChainHeightPayload,
    ReadyPayload,
    RegisterResponsePayload,
    SubmitTransactionPayload,
    TxGossipPayload,
)
from blockchain.core.consensus_params import REGISTRATION_COMMUNITY_ID, ConsensusParams, load_server_pubkey
from blockchain.logging_setup import WarnUnsupportedCurveFilter, get_logger
from blockchain.ports.network import Handler, NetworkPort, RegistrationPort

_POLL_INTERVAL: Final = 2.0
_RETRY_TASK_NAME: Final = "_retry_send"

_SERVER_BLOCKCHAIN_PAYLOADS: Final = frozenset(
    {
        SubmitTransactionPayload,
        GetChainHeightPayload,
        GetBlockPayload,
    }
)
_MEMBER_BLOCKCHAIN_PAYLOADS: Final = frozenset(
    {
        BlockInvPayload,
        GetBlockDataPayload,
        BlockDataPayload,
        TxGossipPayload,
        ReadyPayload,
        GetBlockByHeightPayload,
    }
)

_log = get_logger("net")


def load_my_pubkey(key_path: str | Path) -> bytes:
    private_key = Path(key_path).read_bytes()
    return default_eccrypto.key_from_private_bin(private_key).pub().key_to_bin()


def allow_blockchain_sender(
    payload_cls: type,
    sender: bytes,
    *,
    server_pubkey: bytes,
    member_pubkeys: frozenset[bytes],
) -> bool:
    if payload_cls in _SERVER_BLOCKCHAIN_PAYLOADS:
        return sender == server_pubkey
    if payload_cls in _MEMBER_BLOCKCHAIN_PAYLOADS:
        return sender in member_pubkeys
    raise ValueError(f"unknown blockchain payload class: {payload_cls.__name__}")


def allow_registration_sender(
    payload_cls: type,
    sender: bytes,
    *,
    server_pubkey: bytes,
) -> bool:
    if payload_cls is RegisterResponsePayload:
        return sender == server_pubkey
    raise ValueError(f"unknown registration payload class: {payload_cls.__name__}")


class _PeerAwareCommunity(Community):
    """Shared peer discovery helpers reused from ``client.py``."""

    def __init__(self, settings: CommunitySettings) -> None:
        super().__init__(settings)
        self._peer_waiters: list[tuple[bytes, asyncio.Future[Peer]]] = []

    def started(self) -> None:
        self.register_task(
            "_poll_peers",
            self._poll_peers,
            interval=_POLL_INTERVAL,
            delay=_POLL_INTERVAL,
        )

    async def _poll_peers(self) -> None:
        if not self._peer_waiters:
            return
        for peer in self.get_peers():
            key_bin = peer.public_key.key_to_bin()
            for public_key, future in self._peer_waiters:
                if key_bin == public_key and not future.done():
                    future.set_result(peer)

    def add_peer_waiter(self, public_key: bytes, future: asyncio.Future[Peer]) -> None:
        self._peer_waiters.append((public_key, future))
        for peer in self.get_peers():
            if peer.public_key.key_to_bin() == public_key and not future.done():
                future.set_result(peer)
                break

    async def wait_for_peer(self, public_key: bytes, timeout: float = 120.0) -> Peer:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Peer] = loop.create_future()
        self.add_peer_waiter(public_key, future)
        return await asyncio.wait_for(future, timeout=timeout)

    def _peer_for_pubkey(self, public_key: bytes) -> Peer | None:
        for peer in self.get_peers():
            if peer.public_key.key_to_bin() == public_key:
                return peer
        return None


class BlockchainCommunity(_PeerAwareCommunity):
    """Member-to-member blockchain overlay; implements ``NetworkPort``."""

    community_id = b""

    def __init__(self, settings: CommunitySettings) -> None:
        super().__init__(settings)
        self._server_pubkey = b""
        self._member_pubkeys: frozenset[bytes] = frozenset()
        self._my_pubkey = b""

    def bind_network(
        self,
        *,
        server_pubkey: bytes,
        member_pubkeys: tuple[bytes, ...],
        my_pubkey: bytes,
    ) -> None:
        self._server_pubkey = server_pubkey
        self._member_pubkeys = frozenset(member_pubkeys)
        self._my_pubkey = my_pubkey

    def send(self, peer: bytes, payload: VariablePayload) -> None:
        resolved = self._peer_for_pubkey(peer)
        if resolved is None:
            _log.warning("dropped send: peer %s not online", peer[:8].hex())
            return
        self.ez_send(resolved, payload)

    def broadcast_members(self, payload: VariablePayload) -> None:
        for member in self._member_pubkeys:
            if member == self._my_pubkey:
                continue
            self.send(member, payload)

    def register_handler(self, payload_cls: type, handler: Handler) -> None:
        @lazy_wrapper(payload_cls)
        def _dispatch(community_self: BlockchainCommunity, peer: Peer, payload: Any) -> None:
            sender = peer.public_key.key_to_bin()
            if not allow_blockchain_sender(
                payload_cls,
                sender,
                server_pubkey=community_self._server_pubkey,
                member_pubkeys=community_self._member_pubkeys,
            ):
                _log.debug("dropped unauthorized %s from %s", payload_cls.__name__, sender[:8].hex())
                return
            try:
                handler(sender, payload)
            except Exception:
                _log.exception("handler failed for %s", payload_cls.__name__)

        self.add_message_handler(payload_cls, types.MethodType(_dispatch, self))

    def members_online(self) -> set[bytes]:
        online = {peer.public_key.key_to_bin() for peer in self.get_peers()}
        return online & self._member_pubkeys

    def register_task(self, name: str, fn: Callable[[], Any], interval: float) -> None:
        super().register_task(name, fn, interval=interval, delay=interval)


class RegistrationCommunity(_PeerAwareCommunity):
    """Node-to-server registration overlay; implements ``RegistrationPort``."""

    community_id = b""

    def __init__(self, settings: CommunitySettings) -> None:
        super().__init__(settings)
        self._server_pubkey = b""

    def bind_registration(self, *, server_pubkey: bytes) -> None:
        self._server_pubkey = server_pubkey

    def send_to_server(self, payload: VariablePayload) -> None:
        peer = self._peer_for_pubkey(self._server_pubkey)
        if peer is None:
            _log.warning("dropped registration send: server not online")
            return
        self.ez_send(peer, payload)

    def register_handler(self, payload_cls: type, handler: Handler) -> None:
        @lazy_wrapper(payload_cls)
        def _dispatch(community_self: RegistrationCommunity, peer: Peer, payload: Any) -> None:
            sender = peer.public_key.key_to_bin()
            if not allow_registration_sender(
                payload_cls,
                sender,
                server_pubkey=community_self._server_pubkey,
            ):
                _log.debug("dropped unauthorized %s from %s", payload_cls.__name__, sender[:8].hex())
                return
            try:
                handler(sender, payload)
            except Exception:
                _log.exception("handler failed for %s", payload_cls.__name__)

        self.add_message_handler(payload_cls, types.MethodType(_dispatch, self))

    def server_online(self) -> bool:
        return self._peer_for_pubkey(self._server_pubkey) is not None

    async def wait_for_server(self, timeout: float = 120.0) -> Peer:
        return await self.wait_for_peer(self._server_pubkey, timeout=timeout)

    def start_retry_send_to_server(
        self,
        payload: VariablePayload,
        done: asyncio.Future[Any],
        interval: float = 10.0,
    ) -> None:
        """Send immediately and re-send until ``done`` completes (``client.py`` pattern)."""

        def _retry() -> None:
            if done.done():
                self.cancel_pending_task(_RETRY_TASK_NAME)
                return
            self.send_to_server(payload)

        super().register_task(_RETRY_TASK_NAME, _retry, interval=interval, delay=0)


def _blockchain_community_class(community_id: bytes) -> type[BlockchainCommunity]:
    class_name = f"BlockchainCommunity_{community_id.hex()[:12]}"
    return type(class_name, (BlockchainCommunity,), {"community_id": community_id})


def _registration_community_class(community_id: bytes) -> type[RegistrationCommunity]:
    class_name = f"RegistrationCommunity_{community_id.hex()[:12]}"
    return type(class_name, (RegistrationCommunity,), {"community_id": community_id})


@dataclass(frozen=True, slots=True)
class IPv8NetworkBundle:
    ipv8: IPv8
    network: NetworkPort
    registration: RegistrationPort
    blockchain_overlay: BlockchainCommunity
    registration_overlay: RegistrationCommunity

    async def stop(self) -> None:
        await self.ipv8.stop()


async def build_ipv8(key_path: str | Path, params: ConsensusParams) -> IPv8NetworkBundle:
    """Start one IPv8 instance with blockchain + registration overlays on a shared key."""
    key_path = str(key_path)
    my_pubkey = load_my_pubkey(key_path)
    server_pubkey = load_server_pubkey()
    blockchain_cls = _blockchain_community_class(params.community_id)
    registration_cls = _registration_community_class(REGISTRATION_COMMUNITY_ID)
    blockchain_name = blockchain_cls.__name__
    registration_name = registration_cls.__name__

    builder = ConfigBuilder().clear_keys().clear_overlays()
    builder.add_key("node", "curve25519", key_path)
    walker = [WalkerDefinition(Strategy.RandomWalk, 10, {"timeout": 3.0})]
    for overlay_name in (blockchain_name, registration_name):
        builder.add_overlay(
            overlay_name,
            "node",
            walker,
            default_bootstrap_defs,
            {},
            [("started",)],
        )

    ipv8 = IPv8(
        builder.finalize(),
        extra_communities={
            blockchain_name: blockchain_cls,
            registration_name: registration_cls,
        },
    )
    await ipv8.start()

    blockchain = ipv8.get_overlay(blockchain_cls)
    registration = ipv8.get_overlay(registration_cls)
    blockchain.bind_network(
        server_pubkey=server_pubkey,
        member_pubkeys=params.member_pubkeys,
        my_pubkey=my_pubkey,
    )
    registration.bind_registration(server_pubkey=server_pubkey)

    logging.getLogger("ipv8").addFilter(WarnUnsupportedCurveFilter())

    return IPv8NetworkBundle(
        ipv8=ipv8,
        network=blockchain,
        registration=registration,
        blockchain_overlay=blockchain,
        registration_overlay=registration,
    )
