"""Workflow states and the transitions Relay is allowed to make.

A sent message is not a filled shift, so ``CONTACTING`` and ``AWAITING_RESPONSE`` are
distinct from ``CONFIRMED``. Expiry, delivery failure and source cancellation are
explicit transitions with recorded reasons rather than silent drops.
"""

from __future__ import annotations

DETECTED = "detected"
VALIDATED = "validated"
CONTACTING = "contacting"
AWAITING_RESPONSE = "awaiting_response"
CONFIRMED = "confirmed"
NEEDS_HUMAN = "needs_human"
UNRESOLVED = "unresolved"
WITHDRAWN = "withdrawn"

TERMINAL = {CONFIRMED, UNRESOLVED, WITHDRAWN}
OPEN = {DETECTED, VALIDATED, CONTACTING, AWAITING_RESPONSE, NEEDS_HUMAN}

ALLOWED: dict[str, set[str]] = {
    DETECTED: {VALIDATED, NEEDS_HUMAN, UNRESOLVED, WITHDRAWN},
    VALIDATED: {CONTACTING, NEEDS_HUMAN, UNRESOLVED, WITHDRAWN},
    CONTACTING: {AWAITING_RESPONSE, NEEDS_HUMAN, UNRESOLVED, WITHDRAWN},
    AWAITING_RESPONSE: {CONTACTING, CONFIRMED, NEEDS_HUMAN, UNRESOLVED, WITHDRAWN},
    NEEDS_HUMAN: {CONTACTING, VALIDATED, CONFIRMED, UNRESOLVED, WITHDRAWN},
    CONFIRMED: set(),
    UNRESOLVED: {VALIDATED, WITHDRAWN},
    WITHDRAWN: set(),
}

LABELS = {
    DETECTED: "Detected",
    VALIDATED: "Checked",
    CONTACTING: "Contacting volunteers",
    AWAITING_RESPONSE: "Awaiting response",
    CONFIRMED: "Covered",
    NEEDS_HUMAN: "Needs your decision",
    UNRESOLVED: "Unresolved",
    WITHDRAWN: "Withdrawn",
}


class TransitionError(Exception):
    """Raised when code attempts a transition the state machine does not permit."""


def check(from_state: str, to_state: str) -> None:
    if to_state not in ALLOWED.get(from_state, set()):
        raise TransitionError(f"cannot move workflow from {from_state} to {to_state}")
