"""Deterministic eligibility and outreach policy.

Nothing in this module consults a language model. Every exclusion produces a
machine-readable code and a sentence a coordinator can read, so the UI can always
answer "why wasn't this person asked?".

Times are handled in a single organisation timezone, which the fixtures set to UTC.
That is a documented simplification, not a claim of timezone correctness.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field
from typing import Any

from . import clock, store

DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

DEFAULT_POLICY: dict[str, Any] = {
    "org_name": "Riverside Community Food Pantry (synthetic)",
    "timezone": "UTC",
    "coordinator_name": "Dana (volunteer coordinator)",
    "coordinator_email": "coordinator@relay.test",
    # How many volunteers Relay may contact at once, and in total, per gap.
    "wave_size": 2,
    "max_waves": 2,
    # How long a volunteer has to respond before Relay moves on.
    "response_window_minutes": 25,
    # Contact frequency protection.
    "max_requests_per_week_default": 3,
    "respect_quiet_hours": True,
    # Relay may confirm a replacement without asking the coordinator only when the
    # candidate satisfies every hard rule. Anything else becomes a decision card.
    "auto_confirm_when_fully_eligible": True,
    # Certification is org-verified data. Relay never infers or relaxes it.
    "certification_is_authoritative": True,
    # Do not start outreach for a shift that begins sooner than this.
    "min_lead_time_minutes": 30,
}


@dataclass(frozen=True)
class OrgPolicy:
    version: int
    document: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.document.get(key, DEFAULT_POLICY.get(key, default))

    @property
    def wave_size(self) -> int:
        return int(self.get("wave_size"))

    @property
    def max_waves(self) -> int:
        return int(self.get("max_waves"))

    @property
    def response_window_minutes(self) -> int:
        return int(self.get("response_window_minutes"))

    @property
    def min_lead_time_minutes(self) -> int:
        return int(self.get("min_lead_time_minutes"))

    @property
    def auto_confirm(self) -> bool:
        return bool(self.get("auto_confirm_when_fully_eligible"))

    @property
    def respect_quiet_hours(self) -> bool:
        return bool(self.get("respect_quiet_hours"))


def load_policy() -> OrgPolicy:
    row = store.query_one("SELECT version, document FROM org_policy ORDER BY version DESC LIMIT 1")
    if row is None:
        return OrgPolicy(version=0, document=dict(DEFAULT_POLICY))
    return OrgPolicy(version=int(row["version"]), document=store.loads(row["document"]))


def save_policy(document: dict[str, Any]) -> OrgPolicy:
    """Store a new policy version. Policy is versioned configuration, never memory."""
    merged = dict(DEFAULT_POLICY)
    merged.update(document)
    row = store.query_one("SELECT COALESCE(MAX(version), 0) AS v FROM org_policy")
    version = int(row["v"]) + 1 if row else 1
    store.execute(
        "INSERT INTO org_policy (version, created_at, document) VALUES (?, ?, ?)",
        (version, clock.now_iso(), store.dumps(merged)),
    )
    return OrgPolicy(version=version, document=merged)


# ---------------------------------------------------------------------------
# availability + quiet hours parsing
# ---------------------------------------------------------------------------


def _parse_hhmm(value: str) -> _dt.time:
    hour, minute = value.strip().split(":")
    return _dt.time(int(hour), int(minute))


def _expand_days(spec: str) -> list[int]:
    spec = spec.strip().lower()
    if "-" in spec:
        start, end = spec.split("-", 1)
        if start not in DAY_INDEX or end not in DAY_INDEX:
            return []
        i, j = DAY_INDEX[start], DAY_INDEX[end]
        if i <= j:
            return list(range(i, j + 1))
        return list(range(i, 7)) + list(range(0, j + 1))
    return [DAY_INDEX[spec]] if spec in DAY_INDEX else []


def parse_availability(spec: str) -> list[tuple[list[int], _dt.time, _dt.time]]:
    """Parse ``"mon-fri 08:00-14:00;sat 09:00-13:00"`` into comparable windows."""
    windows: list[tuple[list[int], _dt.time, _dt.time]] = []
    for chunk in spec.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split()
        if len(parts) != 2 or "-" not in parts[1]:
            continue
        days = _expand_days(parts[0])
        start_raw, end_raw = parts[1].split("-", 1)
        try:
            windows.append((days, _parse_hhmm(start_raw), _parse_hhmm(end_raw)))
        except ValueError:
            continue
    return windows


def covers_shift(spec: str, starts_at: _dt.datetime, ends_at: _dt.datetime) -> bool:
    """True when a declared availability window fully contains the shift."""
    windows = parse_availability(spec)
    if not windows:
        return False
    for days, start, end in windows:
        if starts_at.weekday() not in days:
            continue
        if start <= starts_at.time() and ends_at.time() <= end:
            return True
    return False


def in_quiet_hours(spec: str, at: _dt.datetime) -> bool:
    """Quiet hours may wrap midnight, e.g. ``21:00-07:00``."""
    spec = (spec or "").strip()
    if not spec or "-" not in spec:
        return False
    try:
        start_raw, end_raw = spec.split("-", 1)
        start, end = _parse_hhmm(start_raw), _parse_hhmm(end_raw)
    except ValueError:
        return False
    moment = at.time()
    if start <= end:
        return start <= moment < end
    return moment >= start or moment < end


# ---------------------------------------------------------------------------
# candidate evaluation
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    volunteer_id: str
    name: str
    email: str
    certifications: list[str]
    notes: str
    requests_last_7_days: int
    max_requests_per_week: int

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Exclusion:
    volunteer_id: str
    name: str
    codes: list[str]
    explanation: str

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateSet:
    shift_id: str
    shift_version: int
    evaluated_at: str
    policy_version: int
    eligible: list[Candidate] = field(default_factory=list)
    excluded: list[Exclusion] = field(default_factory=list)

    def eligible_ids(self) -> set[str]:
        return {candidate.volunteer_id for candidate in self.eligible}

    def public(self) -> dict[str, Any]:
        return {
            "shift_id": self.shift_id,
            "shift_version": self.shift_version,
            "evaluated_at": self.evaluated_at,
            "policy_version": self.policy_version,
            "eligible": [candidate.public() for candidate in self.eligible],
            "excluded": [exclusion.public() for exclusion in self.excluded],
        }


EXCLUSION_TEXT = {
    "inactive": "is not an active volunteer on the roster",
    "is_cancelling_volunteer": "is the volunteer who cancelled this slot",
    "not_opted_in": "has not opted in to replacement requests",
    "missing_certification": "does not hold the organisation-verified certification this shift requires",
    "unavailable": "has not declared availability covering this shift time",
    "already_assigned": "is already confirmed on an overlapping shift",
    "contact_cap_reached": "has reached the contact limit for this week",
    "quiet_hours": "is inside their quiet hours right now",
    "already_contacted": "has already been contacted about this gap",
}


# Contact caps and quiet hours govern whether Relay may *ask* someone. They must not
# void a volunteer who has already said yes. These are the codes that still block an
# assignment at acceptance time.
BLOCKING_AT_ACCEPTANCE = {
    "inactive",
    "not_opted_in",
    "missing_certification",
    "unavailable",
    "already_assigned",
    "is_cancelling_volunteer",
}


def _split_certs(raw: str) -> list[str]:
    return [item.strip().lower() for item in (raw or "").split(",") if item.strip()]


def _requests_last_7_days(volunteer_id: str, at: _dt.datetime) -> int:
    since = clock.iso(at - _dt.timedelta(days=7))
    row = store.query_one(
        "SELECT COUNT(*) AS n FROM outreach WHERE volunteer_id = ? AND sent_at >= ?",
        (volunteer_id, since),
    )
    return int(row["n"]) if row else 0


def _has_overlapping_assignment(volunteer_id: str, starts_at: str, ends_at: str, shift_id: str) -> bool:
    row = store.query_one(
        """
        SELECT COUNT(*) AS n
        FROM assignments a
        JOIN shifts s ON s.id = a.shift_id
        WHERE a.volunteer_id = ?
          AND a.status = 'confirmed'
          AND s.id != ?
          AND s.starts_at < ?
          AND s.ends_at > ?
        """,
        (volunteer_id, shift_id, ends_at, starts_at),
    )
    return bool(row and int(row["n"]) > 0)


def evaluate_candidates(
    shift_id: str,
    *,
    exclude_volunteer_ids: set[str] | None = None,
    cancelling_volunteer_id: str | None = None,
    already_contacted_ids: set[str] | None = None,
    at: _dt.datetime | None = None,
    policy: OrgPolicy | None = None,
) -> CandidateSet:
    """Score the roster against the shift. Pure reads; no side effects."""
    at = at or clock.now()
    policy = policy or load_policy()
    exclude_volunteer_ids = exclude_volunteer_ids or set()
    already_contacted_ids = already_contacted_ids or set()

    shift = store.query_one("SELECT * FROM shifts WHERE id = ?", (shift_id,))
    if shift is None:
        raise LookupError(f"unknown shift {shift_id}")

    required = (shift["required_certification"] or "").strip().lower()
    starts_at = clock.parse(shift["starts_at"])
    ends_at = clock.parse(shift["ends_at"])

    result = CandidateSet(
        shift_id=shift_id,
        shift_version=int(shift["version"]),
        evaluated_at=clock.iso(at),
        policy_version=policy.version,
    )

    for row in store.query("SELECT * FROM volunteers ORDER BY name"):
        codes: list[str] = []
        certifications = _split_certs(row["certifications"])
        sent_recently = _requests_last_7_days(row["id"], at)
        # A cap of 0 is meaningful ("pause my requests"), so do not fall back on falsiness.
        raw_cap = row["max_requests_per_week"]
        cap = int(raw_cap) if raw_cap is not None else int(policy.get("max_requests_per_week_default"))

        if not int(row["active"]):
            codes.append("inactive")
        if cancelling_volunteer_id and row["id"] == cancelling_volunteer_id:
            codes.append("is_cancelling_volunteer")
        if row["id"] in exclude_volunteer_ids:
            codes.append("already_assigned")
        if not int(row["opted_in"]):
            codes.append("not_opted_in")
        if required and required not in certifications:
            codes.append("missing_certification")
        if not covers_shift(row["availability"], starts_at, ends_at):
            codes.append("unavailable")
        if _has_overlapping_assignment(row["id"], shift["starts_at"], shift["ends_at"], shift_id):
            codes.append("already_assigned")
        if sent_recently >= cap:
            codes.append("contact_cap_reached")
        if policy.respect_quiet_hours and in_quiet_hours(row["quiet_hours"], at):
            codes.append("quiet_hours")
        if row["id"] in already_contacted_ids:
            codes.append("already_contacted")

        if codes:
            unique = sorted(set(codes))
            explanation = f"{row['name']} " + "; ".join(EXCLUSION_TEXT[code] for code in unique) + "."
            result.excluded.append(
                Exclusion(volunteer_id=row["id"], name=row["name"], codes=unique, explanation=explanation)
            )
        else:
            result.eligible.append(
                Candidate(
                    volunteer_id=row["id"],
                    name=row["name"],
                    email=row["email"],
                    certifications=certifications,
                    notes=row["notes"] or "",
                    requests_last_7_days=sent_recently,
                    max_requests_per_week=cap,
                )
            )

    return result


def lead_time_ok(shift_starts_at: str, at: _dt.datetime, policy: OrgPolicy) -> bool:
    return clock.parse(shift_starts_at) - at >= _dt.timedelta(minutes=policy.min_lead_time_minutes)
