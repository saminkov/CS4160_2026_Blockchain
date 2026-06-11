from __future__ import annotations

import time

from blockchain.ports.clock import ClockPort


class SystemClock(ClockPort):
    """Real wall-clock time, truncated to whole seconds."""

    def now(self) -> int:
        return int(time.time())


class FakeClock(ClockPort):
    """A manually controlled clock for deterministic tests."""

    def __init__(self, start: int = 0) -> None:
        if start < 0:
            raise ValueError("start must be non-negative")
        self._now = start

    def now(self) -> int:
        return self._now

    def advance(self, seconds: int) -> None:
        """Move the clock forward by ``seconds`` (must keep time non-negative)."""
        if self._now + seconds < 0:
            raise ValueError("clock cannot go negative")
        self._now += seconds

    def set(self, t: int) -> None:
        """Jump the clock to absolute time ``t`` seconds."""
        if t < 0:
            raise ValueError("time must be non-negative")
        self._now = t
