from __future__ import annotations

from typing import Protocol


class ClockPort(Protocol):

    def now(self) -> int:
        """Returns the current time as a Unix timestamp"""
        ...