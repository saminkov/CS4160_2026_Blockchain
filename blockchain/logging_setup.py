"""Logging bootstrap: per-component loggers and ``[member h=height]`` prefix (A21)."""

from __future__ import annotations

import logging
from typing import Final

COMPONENTS: Final = ("core", "net", "chain", "miner", "sync", "reg")

_UNSUPPORTED_CURVE_PHRASES: Final = (
    "unsupported curve",
    "unsupported public key",
    "could not deserialize key",
)


class WarnUnsupportedCurveFilter(logging.Filter):
    """Drop noisy IPv8 warnings about foreign peers on unsupported curves."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage().lower()
        return not any(phrase in message for phrase in _UNSUPPORTED_CURVE_PHRASES)


class _LoggingState:
    member: str = "node"
    height: int = 0


_state = _LoggingState()
_configured = False


class _MemberHeightFilter(logging.Filter):
    def __init__(self, state: _LoggingState) -> None:
        super().__init__()
        self._state = state

    def filter(self, record: logging.LogRecord) -> bool:
        setattr(record, "member", self._state.member)
        setattr(record, "height", self._state.height)
        return True


def configure_logging(
    *,
    level: int = logging.INFO,
    member: str = "node",
    height: int = 0,
) -> None:
    """Configure the ``blockchain.*`` logger tree once per process."""
    global _configured

    _state.member = member
    _state.height = height

    root = logging.getLogger("blockchain")
    root.setLevel(level)

    if _configured:
        return

    handler = logging.StreamHandler()
    handler.addFilter(_MemberHeightFilter(_state))
    handler.addFilter(WarnUnsupportedCurveFilter())
    handler.setFormatter(
        logging.Formatter("[%(member)s h=%(height)s] %(name)s: %(levelname)s: %(message)s")
    )
    root.addHandler(handler)
    _configured = True


def get_logger(component: str) -> logging.Logger:
    """Return a per-component logger under ``blockchain.<component>``."""
    if component not in COMPONENTS:
        raise ValueError(f"unknown log component: {component}")
    return logging.getLogger(f"blockchain.{component}")


def set_logging_member(member: str) -> None:
    _state.member = member


def set_logging_height(height: int) -> None:
    _state.height = height
