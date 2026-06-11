from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

# Called on the event loop with (nonce, generation) when a valid nonce is found.
OnFound = Callable[[int, int], None]


class MinerPort(Protocol):
    """A cancellable, off-loop proof-of-work search engine."""

    def mine(
        self,
        header_prefix: bytes,
        difficulty: int,
        generation: int,
        on_found: OnFound,
    ) -> None:
        """Search for a nonce over the 76-byte ``header_prefix``.

        Replaces any in-flight job. Invokes ``on_found(nonce, generation)`` once
        a nonce satisfying ``difficulty`` leading-zero bits is found.
        """
        ...

    def cancel(self) -> None:
        """Abandon the current search without stopping the worker process."""
        ...

def shutdown(self) -> None:
        """Stop the worker process and release its resources."""
        ...
