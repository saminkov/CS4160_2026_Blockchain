import asyncio
import logging
import types
from typing import Any, Callable

from ipv8.community import Community, CommunitySettings
from ipv8.configuration import ConfigBuilder, Strategy, WalkerDefinition, default_bootstrap_defs
from ipv8.lazy_community import lazy_wrapper
from ipv8.messaging.payload_dataclass import VariablePayload
from ipv8.peer import Peer
from ipv8_service import IPv8

_POLL_INTERVAL = 2.0

_logger = logging.getLogger(__name__)


class _WarnUnsupportedCurve(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if "UnsupportedAlgorithm" in (record.exc_text or "") or \
                "UnsupportedAlgorithm" in str(record.getMessage()):
            _logger.warning("Unsupported algorithm found from a peer key — skipping")
            return False
        return True



class _ManagedCommunity(Community):
    """Generic community used internally by IPv8Client. Not for direct use."""

    community_id = b""

    def __init__(self, settings: CommunitySettings) -> None:
        super().__init__(settings)
        self._peer_waiters: list[tuple[bytes, asyncio.Future]] = []
        self._discovered: dict[bytes, Peer] = {}

    def started(self) -> None:
        self.register_task("_poll_peers", self._poll_peers,
                           interval=_POLL_INTERVAL, delay=_POLL_INTERVAL)

    async def _poll_peers(self) -> None:
        for peer in self.get_peers():
            key_bin = peer.public_key.key_to_bin()
            self._discovered[key_bin] = peer
            for pub_key, future in self._peer_waiters:
                if key_bin == pub_key and not future.done():
                    future.set_result(peer)

    def add_peer_waiter(self, public_key: bytes, future: "asyncio.Future[Peer]") -> None:
        self._peer_waiters.append((public_key, future))


class IPv8Client:
    """Manages an IPv8 node and provides peer discovery and authenticated messaging."""

    def __init__(self, ipv8: IPv8, community: _ManagedCommunity) -> None:
        self._ipv8 = ipv8
        self._community = community

    @classmethod
    async def build(cls, community_id: bytes, key_path: str) -> "IPv8Client":
        """Start an IPv8 node joined to community_id, using the curve25519 key at key_path."""
        cls_name = f"_MC_{community_id.hex()[:12]}"
        community_cls = type(cls_name, (_ManagedCommunity,), {"community_id": community_id})
        logging.getLogger(cls_name).addFilter(_WarnUnsupportedCurve())

        builder = ConfigBuilder().clear_keys().clear_overlays()
        builder.add_key("node", "curve25519", key_path)
        builder.add_overlay(
            cls_name,
            "node",
            [WalkerDefinition(Strategy.RandomWalk, 10, {"timeout": 3.0})],
            default_bootstrap_defs,
            {},
            [("started",)],
        )

        ipv8 = IPv8(builder.finalize(), extra_communities={cls_name: community_cls})
        await ipv8.start()
        community = ipv8.get_overlay(community_cls)
        return cls(ipv8, community)

    async def stop(self) -> None:
        await self._ipv8.stop()

    async def __aenter__(self) -> "IPv8Client":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    def on_message(self, payload_class: type) -> Callable:
        """Decorator that registers handler(peer: Peer, payload) for incoming payload_class messages."""
        def decorator(handler: Callable[[Peer, Any], None]) -> Callable:
            @lazy_wrapper(payload_class)
            def _dispatch(community_self: _ManagedCommunity, peer: Peer, payload: Any) -> None:
                handler(peer, payload)

            self._community.add_message_handler(
                payload_class,
                types.MethodType(_dispatch, self._community),
            )
            return handler
        return decorator

    async def wait_for_peer(self, public_key: bytes, timeout: float = 120.0) -> Peer:
        """Resolve to the first verified Peer whose public key matches, or raise TimeoutError."""
        future: asyncio.Future[Peer] = asyncio.get_running_loop().create_future()
        self._community.add_peer_waiter(public_key, future)
        return await asyncio.wait_for(future, timeout=timeout)

    async def discover_peer(self, public_key: bytes, timeout: float = 120.0) -> Peer:
        """Wait for a peer to be discovered and cache it; return the Peer."""
        return await self.wait_for_peer(public_key, timeout=timeout)

    async def discover_peers(
        self, public_keys: list[bytes], timeout: float = 120.0
    ) -> dict[bytes, Peer]:
        """Discover multiple peers concurrently; return a pubkey→Peer mapping."""
        peers = await asyncio.gather(
            *(self.discover_peer(pk, timeout=timeout) for pk in public_keys)
        )
        return dict(zip(public_keys, peers))

    def send(self, peer: "Peer | bytes", payload: VariablePayload) -> None:
        """Send an authenticated payload to peer. peer may be a Peer or a cached public-key bytes."""
        if isinstance(peer, bytes):
            peer = self._community._discovered[peer]
        self._community.ez_send(peer, payload)

    def start_retry_send(self, peer: "Peer | bytes", payload: VariablePayload,
                         done: "asyncio.Future[Any]", interval: float = 10.0) -> None:
        """Send payload immediately and re-send every interval seconds until done resolves."""
        if isinstance(peer, bytes):
            peer = self._community._discovered[peer]

        def _retry() -> None:
            if done.done():
                self._community.cancel_pending_task("_retry_send")
                return
            self._community.ez_send(peer, payload)

        self._community.register_task("_retry_send", _retry, interval=interval, delay=0)

    async def validate_visible(self, public_key: bytes, timeout: float = 120.0) -> bool:
        """Return True if a peer with public_key joins the community within timeout seconds."""
        try:
            await self.wait_for_peer(public_key, timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
