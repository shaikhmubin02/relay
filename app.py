"""Serverless entry point (Vercel).

Vercel's Python runtime loads the top-level ``app`` from this file and routes every
request to it. Nothing here is part of Relay itself — it only adjusts the three
things a serverless host changes about the environment:

* the filesystem is read-only apart from ``/tmp``, so the database lives there;
* the process is frozen between requests, so the background worker is off and
  ``scheduler.tick`` runs on page loads instead (``RELAY_WORKER_ENABLED=0``);
* there is no persistent disk, so a cold start seeds the demo organisation.

All three are stated in a banner on every page of the hosted instance rather than
left for a reviewer to discover. Run Relay locally for the persistent version.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("RELAY_DB", "/tmp/relay/relay.db")
os.environ.setdefault("RELAY_WORKER_ENABLED", "0")
os.environ.setdefault("RELAY_AUTOSEED", "1")
os.environ.setdefault("RELAY_EPHEMERAL", "1")
# The hosted demo never sends mail. relay.test is reserved by RFC 2606 and is not
# routable, so a misconfiguration cannot reach a real inbox.
os.environ.setdefault("RELAY_EMAIL_TRANSPORT", "fake")
os.environ.setdefault("RELAY_EMAIL_ALLOWLIST_DOMAINS", "relay.test")

from relay.api import app  # noqa: E402  (path setup must run first)

__all__ = ["app"]
