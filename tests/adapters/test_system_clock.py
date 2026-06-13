from __future__ import annotations

import pytest

from blockchain.adapters.system_clock import FakeClock, SystemClock
from blockchain.ports.clock import ClockPort

# mypy conformance: both adapters must satisfy the port.
_s: ClockPort = SystemClock()
_f: ClockPort = FakeClock()


class TestSystemClock:
    def test_now_is_int_truncated_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("time.time", lambda: 1_700_000_000.9)
        now = SystemClock().now()
        assert now == 1_700_000_000
        assert isinstance(now, int)

    def test_now_is_non_decreasing(self) -> None:
        clock = SystemClock()
        assert clock.now() <= clock.now()


class TestFakeClock:
    def test_starts_at_given_time(self) -> None:
        assert FakeClock(start=1_700_000_000).now() == 1_700_000_000

    def test_defaults_to_zero(self) -> None:
        assert FakeClock().now() == 0

    def test_advance_steps_forward(self) -> None:
        clock = FakeClock(start=1_700_000_000)
        clock.advance(120)
        assert clock.now() == 1_700_000_120

    def test_advance_can_rewind_within_range(self) -> None:
        clock = FakeClock(start=100)
        clock.advance(-40)
        assert clock.now() == 60

    def test_set_jumps_to_absolute_time(self) -> None:
        clock = FakeClock(start=1_700_000_000)
        clock.set(0)
        assert clock.now() == 0

    def test_rejects_negative_start(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            FakeClock(start=-1)

    def test_rejects_set_negative(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            FakeClock().set(-1)

    def test_rejects_advance_past_zero(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            FakeClock(start=10).advance(-11)
