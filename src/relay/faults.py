"""Deliberate fault injection for tests, evaluation and the demo.

Failure handling is the part of Relay worth demonstrating, so failures have to be
reproducible on command. Faults are armed explicitly and are always visible in the
audit trail; nothing here changes behaviour unless a fault is armed.
"""

from __future__ import annotations

import threading

KNOWN_FAULTS = {
    "delivery_unknown": "Transport returns an indeterminate result for the next send.",
    "delivery_error": "Transport raises for the next send.",
    "model_error": "The model provider raises on the next call.",
}

_lock = threading.Lock()
_armed: dict[str, int] = {}


def arm(name: str, times: int = 1) -> None:
    if name not in KNOWN_FAULTS:
        raise ValueError(f"unknown fault {name}")
    with _lock:
        _armed[name] = _armed.get(name, 0) + times


def disarm(name: str | None = None) -> None:
    with _lock:
        if name is None:
            _armed.clear()
        else:
            _armed.pop(name, None)


def armed() -> dict[str, int]:
    with _lock:
        return dict(_armed)


def consume(name: str) -> bool:
    """Return True at most ``times`` times after arming, then fall back to normal."""
    with _lock:
        remaining = _armed.get(name, 0)
        if remaining <= 0:
            return False
        if remaining == 1:
            _armed.pop(name, None)
        else:
            _armed[name] = remaining - 1
        return True
