"""Load the synthetic demo organisation from CSV.

The CSV shape here is the documented import format. Everything in ``data/fixtures``
is invented: the names, the addresses (all on the non-routable ``relay.test``
domain), the availability, and the notes. No real volunteer data is in this
repository and none should be added to it.

Time columns accept either an absolute ISO-8601 timestamp or a relative form
``+<days> HH:MM``, resolved against the current demo day. The fixtures use the
relative form so a fresh clone always has a shift later today.
"""

from __future__ import annotations

import csv
import datetime as _dt
import re
from pathlib import Path
from typing import Any

from . import clock, democlock, ids, policy as policy_mod, store
from .config import REPO_ROOT

FIXTURES = REPO_ROOT / "data" / "fixtures"
_RELATIVE = re.compile(r"^\s*([+-]\d+)\s+(\d{1,2}):(\d{2})\s*$")

# The story in docs/DEMO_SCRIPT.md opens here: a cancellation at 08:10 for a 10:00 shift.
DEMO_START_TIME = _dt.time(8, 10)


def resolve_time(value: str, *, base: _dt.datetime | None = None) -> str:
    """Turn a fixture time column into a stored ISO-8601 UTC timestamp."""
    base = base or clock.now()
    match = _RELATIVE.match(value)
    if match is None:
        return clock.iso(clock.parse(value))
    days, hour, minute = int(match.group(1)), int(match.group(2)), int(match.group(3))
    day = (base + _dt.timedelta(days=days)).date()
    return clock.iso(_dt.datetime.combine(day, _dt.time(hour, minute), tzinfo=_dt.timezone.utc))


def _read(name: str, directory: Path) -> list[dict[str, str]]:
    with (directory / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _anchor(shift_rows: list[dict[str, str]], base: _dt.datetime, min_lead_minutes: int) -> _dt.datetime:
    """Roll the demo day forward until the first shift is comfortably in the future.

    The fixtures describe a morning-to-evening day. Rather than rewrite them, the
    seeder moves the whole day forward in 24h steps, so a clone seeded at 4pm still
    gets a demo with a shift that has not already started.
    """
    deadline = base + _dt.timedelta(minutes=min_lead_minutes)
    for _ in range(8):
        starts = [clock.parse(resolve_time(row["starts_at"], base=base)) for row in shift_rows]
        if starts and min(starts) >= deadline:
            return base
        base = base + _dt.timedelta(days=1)
    return base


def seed(
    *,
    directory: Path | None = None,
    reset: bool = True,
    base: _dt.datetime | None = None,
    anchor: bool = True,
    min_lead_minutes: int = 90,
    demo_clock: bool = True,
    policy_overrides: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Rebuild the demo organisation. Returns row counts."""
    directory = directory or FIXTURES
    if reset:
        store.reset_db()
    else:
        store.init_db()

    if demo_clock:
        democlock.set_offset(0.0)
        democlock.install()
    base = base or clock.now()
    policy_mod.save_policy(policy_overrides or {})

    volunteers = _read("volunteers.csv", directory)
    shifts = _read("shifts.csv", directory)
    assignments = _read("assignments.csv", directory)

    if anchor:
        base = _anchor(shifts, base, min_lead_minutes)

    with store.write_tx() as conn:
        for row in volunteers:
            conn.execute(
                """
                INSERT INTO volunteers (id, name, email, active, opted_in, certifications, availability,
                                        quiet_hours, max_requests_per_week, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["name"],
                    row["email"],
                    int(row.get("active", 1)),
                    int(row.get("opted_in", 0)),
                    row.get("certifications", ""),
                    row.get("availability", ""),
                    row.get("quiet_hours", "22:00-06:00"),
                    int(row.get("max_requests_per_week", 3)),
                    row.get("notes", ""),
                ),
            )
        for row in shifts:
            conn.execute(
                """
                INSERT INTO shifts (id, title, location, starts_at, ends_at, required_certification,
                                    headcount, version, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    row["id"],
                    row["title"],
                    row.get("location", ""),
                    resolve_time(row["starts_at"], base=base),
                    resolve_time(row["ends_at"], base=base),
                    row.get("required_certification", ""),
                    int(row.get("headcount", 1)),
                    row.get("notes", ""),
                ),
            )
        for row in assignments:
            conn.execute(
                """
                INSERT INTO assignments (id, shift_id, volunteer_id, slot_no, status, source, created_at)
                VALUES (?, ?, ?, ?, 'confirmed', 'roster', ?)
                """,
                (
                    ids.new_id("asg"),
                    row["shift_id"],
                    row["volunteer_id"],
                    int(row.get("slot_no", 1)),
                    clock.iso(base),
                ),
            )

    if demo_clock:
        first_start = clock.parse(
            store.query_one("SELECT MIN(starts_at) AS m FROM shifts")["m"]
        )
        democlock.align_to(_dt.datetime.combine(first_start.date(), DEMO_START_TIME, tzinfo=_dt.timezone.utc))
        democlock.install()

    return {"volunteers": len(volunteers), "shifts": len(shifts), "assignments": len(assignments)}
