from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

# Handler invoked with (sender_public_key, decoded_payload).
Handler = Callable[[bytes, Any], None]


class NetworkPort(Protocol):
    """The member-to-member blockchain overlay."""

    def send(self, peer: bytes, payload: Any) -> None:
        """Send an authenticated payload to the member with this public key."""
        ...

    def broadcast_members(self, payload: Any) -> None:
        """Send a payload to every known group member."""
        ...

    def register_handler(self, payload_cls: type, handler: Handler) -> None:
        """Route incoming ``payload_cls`` messages to ``handler``."""
        ...

    def members_online(self) -> set[bytes]:
        """Return the public keys of group members currently reachable."""
        ...

    def register_task(self, name: str, fn: Callable[[], Any], interval: float) -> None:
        """Schedule ``fn`` to run every ``interval`` seconds under ``name``."""
        ...


class RegistrationPort(Protocol):
    """The node-to-server registration overlay."""

    def send_to_server(self, payload: Any) -> None:
        """Send an authenticated payload to the Lab3 server."""
        ...

    def register_handler(self, payload_cls: type, handler: Handler) -> None:
        """Route incoming ``payload_cls`` messages to ``handler``."""
        ...

    def server_online(self) -> bool:
        """Return True iff the server peer is currently reachable."""
        ...
