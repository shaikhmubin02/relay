"""The labelled demo clock.

The seeded scenario is a fixture with a story that starts at 08:10 and involves a
25-minute response window. Two things would otherwise make it unusable as a demo:
a judge opening it at 03:00 UTC would find every volunteer inside their quiet hours,
and demonstrating the no-response path would take 25 real minutes.

So the demo runs on real time plus a stored offset. Time still moves forward on its
own, deadlines still expire by themselves, and the offset is displayed in the header
of every page. Setting the offset to zero returns Relay to plain wall-clock time.
"""

from __future__ import annotations

import datetime as _dt

from . import clock, store

OFFSET_KEY = "demo_clock_offset_seconds"


def _read_offset() -> float:
    try:
        row = store.query_one("SELECT value FROM runtime_state WHERE key = ?", (OFFSET_KEY,))
    except Exception:  # noqa: BLE001 - the table may not exist yet during first boot
        return 0.0
    return float(row["value"]) if row else 0.0


def offset_seconds() -> float:
    return _read_offset()


def set_offset(seconds: float) -> None:
    store.execute(
        "INSERT INTO runtime_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (OFFSET_KEY, str(float(seconds))),
    )


def advance(minutes: float) -> float:
    """Skip forward. Used by the demo control and by the evaluation harness."""
    new_offset = _read_offset() + minutes * 60.0
    set_offset(new_offset)
    return new_offset


def align_to(target: _dt.datetime) -> float:
    """Set the offset so that ``clock.now()`` reads as ``target`` right now."""
    delta = (target - _dt.datetime.now(_dt.timezone.utc)).total_seconds()
    set_offset(delta)
    return delta


def install(*, force: bool = False) -> None:
    """Point the global clock at real-time-plus-offset, if an offset is stored.

    With no offset there is nothing to apply, and installing anyway would quietly
    replace a clock the caller chose deliberately -- which is exactly what tests and
    the evaluation harness do.
    """
    if force or abs(_read_offset()) > 0.0:
        clock.set_clock(clock.OffsetClock(_read_offset))


def describe() -> dict[str, object]:
    seconds = _read_offset()
    return {
        "active": abs(seconds) > 1.0,
        "offset_seconds": seconds,
        "offset_human": _human(seconds),
        "demo_now": clock.now_iso(),
        "real_now": clock.iso(_dt.datetime.now(_dt.timezone.utc)),
    }


def _human(seconds: float) -> str:
    if abs(seconds) < 60:
        return "none"
    minutes = int(round(seconds / 60))
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    parts = [f"{days}d" for _ in range(1) if days] + [f"{hours}h" for _ in range(1) if hours] + [f"{minutes}m" for _ in range(1) if minutes]
    return sign + " ".join(parts) if parts else "none"
