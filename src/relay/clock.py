"""Time source.

Every deadline in Relay is persisted as an absolute timestamp and re-checked by a
worker. Nothing sleeps in memory. Tests drive time by installing a FrozenClock.
"""

from __future__ import annotations

import datetime as _dt
import threading


class Clock:
    """Wall-clock time in UTC."""

    def now(self) -> _dt.datetime:
        return _dt.datetime.now(_dt.timezone.utc)


class FrozenClock(Clock):
    """Deterministic clock for tests, evaluation and scripted demos."""

    def __init__(self, start: _dt.datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._now = start.astimezone(_dt.timezone.utc)
        self._lock = threading.Lock()

    def now(self) -> _dt.datetime:
        with self._lock:
            return self._now

    def advance(self, **delta: float) -> _dt.datetime:
        with self._lock:
            self._now = self._now + _dt.timedelta(**delta)
            return self._now

    def set(self, when: _dt.datetime) -> None:
        with self._lock:
            self._now = when.astimezone(_dt.timezone.utc)


class OffsetClock(Clock):
    """Real time plus a persisted offset.

    The seeded demo needs to reproduce the same 08:10 scenario whether a judge opens
    it at breakfast or at midnight, and it needs a way to skip a 25-minute response
    window without anyone waiting. An offset does both while keeping time monotonic:
    deadlines still pass, expiries still fire, nothing is faked retroactively.
    """

    def __init__(self, offset_seconds_provider) -> None:
        self._provider = offset_seconds_provider

    def now(self) -> _dt.datetime:
        return _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=self._provider())


_clock: Clock = Clock()


def set_clock(clock: Clock) -> None:
    global _clock
    _clock = clock


def get_clock() -> Clock:
    return _clock


def now() -> _dt.datetime:
    return _clock.now()


def iso(value: _dt.datetime) -> str:
    """Canonical string form used in the database and in receipts."""
    return value.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat()


def parse(value: str) -> _dt.datetime:
    parsed = _dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def now_iso() -> str:
    return iso(now())
