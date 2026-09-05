"""Runtime configuration.

Everything that could send a message, spend money, or touch a real inbox is off by
default. A fresh clone runs end to end with no credentials and no network.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "var" / "relay.db"


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return list(default)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _public_base_url() -> str:
    """Where volunteers' links should point.

    On a platform that assigns the hostname at deploy time, guessing wrong means
    every accept link in every email points at localhost, so fall back to the
    platform-provided domain before the local default.
    """
    explicit = os.environ.get("RELAY_PUBLIC_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    for candidate in ("VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL"):
        host = os.environ.get(candidate, "").strip()
        if host:
            return f"https://{host.rstrip('/')}"
    return "http://127.0.0.1:8000"


@dataclass(frozen=True)
class Settings:
    """Process-wide settings resolved from the environment."""

    db_path: Path = field(default_factory=lambda: Path(os.environ.get("RELAY_DB", str(DEFAULT_DB))))

    # --- model -------------------------------------------------------------
    # "auto"    : use Bedrock when credentials + RELAY_MODEL_ID are present, else offline
    # "bedrock" : require Bedrock; fail loudly if unavailable
    # "offline" : never call a network model (default for tests and judge runs)
    model_provider: str = field(default_factory=lambda: os.environ.get("RELAY_MODEL_PROVIDER", "auto").lower())
    # Bedrock inference-profile id: "<prefix>.anthropic.<model>", e.g. global/us/eu/apac.
    # Run `python -m relay check-model --list` to see what your account can actually reach.
    model_id: str = field(default_factory=lambda: os.environ.get("RELAY_MODEL_ID", "global.anthropic.claude-opus-5"))
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "us-west-2"))

    # --- spend + blast-radius limits --------------------------------------
    max_model_calls_per_workflow: int = field(default_factory=lambda: _int("RELAY_MAX_MODEL_CALLS", 8))
    max_tool_calls_per_workflow: int = field(default_factory=lambda: _int("RELAY_MAX_TOOL_CALLS", 24))
    max_emails_per_workflow: int = field(default_factory=lambda: _int("RELAY_MAX_EMAILS", 6))

    # --- outbound delivery --------------------------------------------------
    # fake : store the message and render it in the built-in test inbox (default)
    # smtp : real delivery through an SMTP relay, allowlist enforced
    email_transport: str = field(default_factory=lambda: os.environ.get("RELAY_EMAIL_TRANSPORT", "fake").lower())
    email_from: str = field(default_factory=lambda: os.environ.get("RELAY_EMAIL_FROM", "relay@relay.test"))
    email_allowlist_domains: list[str] = field(
        default_factory=lambda: _list("RELAY_EMAIL_ALLOWLIST_DOMAINS", ["relay.test"])
    )
    smtp_host: str = field(default_factory=lambda: os.environ.get("RELAY_SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: _int("RELAY_SMTP_PORT", 587))
    smtp_user: str = field(default_factory=lambda: os.environ.get("RELAY_SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.environ.get("RELAY_SMTP_PASSWORD", ""))
    smtp_starttls: bool = field(default_factory=lambda: _bool("RELAY_SMTP_STARTTLS", True))

    # --- security -----------------------------------------------------------
    token_secret: str = field(default_factory=lambda: os.environ.get("RELAY_TOKEN_SECRET", "dev-only-insecure-secret"))
    coordinator_token: str = field(default_factory=lambda: os.environ.get("RELAY_COORDINATOR_TOKEN", "dev-coordinator-token"))
    intake_token: str = field(default_factory=lambda: os.environ.get("RELAY_INTAKE_TOKEN", "dev-intake-token"))
    public_base_url: str = field(default_factory=_public_base_url)

    # --- worker -------------------------------------------------------------
    worker_enabled: bool = field(default_factory=lambda: _bool("RELAY_WORKER_ENABLED", True))
    worker_interval_seconds: int = field(default_factory=lambda: _int("RELAY_WORKER_INTERVAL_SECONDS", 5))

    # --- hosted demo ---------------------------------------------------------
    # On a serverless host there is no long-lived process and no persistent disk,
    # so the database is seeded on cold start and the worker runs inline on page
    # loads instead of on a thread. Both are stated in the UI rather than hidden.
    autoseed: bool = field(default_factory=lambda: _bool("RELAY_AUTOSEED", False))
    ephemeral: bool = field(default_factory=lambda: _bool("RELAY_EPHEMERAL", False))

    @property
    def using_default_secrets(self) -> bool:
        return self.token_secret == "dev-only-insecure-secret" or self.coordinator_token == "dev-coordinator-token"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(settings: Settings) -> None:
    global _settings
    _settings = settings


def reset_settings() -> None:
    global _settings
    _settings = None
