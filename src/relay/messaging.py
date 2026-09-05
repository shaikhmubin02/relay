"""Outbound messages: approved templates, an allowlist, and a durable outbox.

Three rules hold regardless of what the model asks for:

1. Relay only sends one of the templates defined here. The model may contribute a
   short plain-text line, which is sanitised before it is inserted.
2. Every recipient domain is checked against the allowlist at enqueue time and again
   at send time.
3. A message is written to the outbox inside the same transaction as the state change
   that justifies it, keyed by an idempotency key. A replay cannot duplicate a send.
"""

from __future__ import annotations

import re
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol

from . import audit, clock, faults, ids, store
from .config import get_settings

MAX_PERSONAL_NOTE_CHARS = 220
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class MessagingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DeliveryUnknown(Exception):
    """The transport neither confirmed nor refused. Never retried blindly."""


# ---------------------------------------------------------------------------
# sanitising model-supplied text
# ---------------------------------------------------------------------------


def sanitise_personal_note(raw: str | None) -> str:
    """Strip links, control characters and length from model-written prose.

    The model is allowed to be warm. It is not allowed to add a second link, a
    phone number to call, or an instruction that contradicts the template.
    """
    if not raw:
        return ""
    text = _CONTROL_RE.sub(" ", str(raw))
    text = _URL_RE.sub("", text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_PERSONAL_NOTE_CHARS:
        text = text[: MAX_PERSONAL_NOTE_CHARS - 1].rstrip() + "…"
    return text


# ---------------------------------------------------------------------------
# approved templates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Template:
    template_id: str
    subject: str
    body: str
    required: tuple[str, ...]


TEMPLATES: dict[str, Template] = {
    "coverage_request_v1": Template(
        template_id="coverage_request_v1",
        subject="Can you cover {shift_title} on {shift_when}?",
        body=(
            "Hi {volunteer_name},\n"
            "\n"
            "A slot opened up at {org_name}:\n"
            "\n"
            "  {shift_title}\n"
            "  {shift_when}\n"
            "  {shift_location}\n"
            "\n"
            "{personal_note}"
            "You are on the list for this role, so I am asking you first. No pressure at all -- "
            "if now is not a good time, decline and I will ask someone else.\n"
            "\n"
            "  Yes, I can cover it:  {accept_url}\n"
            "  No, not this time:    {decline_url}\n"
            "\n"
            "These links work until {expires_when} and are just for you.\n"
            "\n"
            "-- Relay, on behalf of {org_name}\n"
            "This is an automated coverage request sent to volunteers who opted in. "
            "Reply to this address to change your preferences.\n"
        ),
        required=(
            "volunteer_name",
            "org_name",
            "shift_title",
            "shift_when",
            "shift_location",
            "accept_url",
            "decline_url",
            "expires_when",
        ),
    ),
    "coverage_confirmed_volunteer_v1": Template(
        template_id="coverage_confirmed_volunteer_v1",
        subject="Confirmed: {shift_title} on {shift_when}",
        body=(
            "Hi {volunteer_name},\n"
            "\n"
            "You are confirmed for:\n"
            "\n"
            "  {shift_title}\n"
            "  {shift_when}\n"
            "  {shift_location}\n"
            "\n"
            "Thank you for stepping in. If something changes, contact the coordinator at "
            "{org_name} as early as you can.\n"
            "\n"
            "-- Relay, on behalf of {org_name}\n"
        ),
        required=("volunteer_name", "org_name", "shift_title", "shift_when", "shift_location"),
    ),
    "coverage_taken_v1": Template(
        template_id="coverage_taken_v1",
        subject="That shift is already covered -- thank you",
        body=(
            "Hi {volunteer_name},\n"
            "\n"
            "Thank you for offering to cover {shift_title} on {shift_when}. Someone else "
            "answered a moment before you did, so the slot is filled and you are not "
            "scheduled for it.\n"
            "\n"
            "Nothing further is needed from you.\n"
            "\n"
            "-- Relay, on behalf of {org_name}\n"
        ),
        required=("volunteer_name", "org_name", "shift_title", "shift_when"),
    ),
    "coordinator_receipt_v1": Template(
        template_id="coordinator_receipt_v1",
        subject="{outcome_label}: {shift_title} on {shift_when}",
        body=(
            "{summary}\n"
            "\n"
            "Full receipt: {receipt_url}\n"
            "\n"
            "-- Relay\n"
        ),
        required=("outcome_label", "shift_title", "shift_when", "summary", "receipt_url"),
    ),
}


def render(template_id: str, context: dict[str, Any]) -> tuple[str, str]:
    template = TEMPLATES.get(template_id)
    if template is None:
        raise MessagingError("unknown_template", f"template {template_id} is not approved")
    missing = [key for key in template.required if not context.get(key)]
    if missing:
        raise MessagingError("missing_fields", f"template {template_id} missing {', '.join(missing)}")

    values = dict(context)
    note = sanitise_personal_note(values.get("personal_note"))
    values["personal_note"] = f"{note}\n\n" if note else ""
    values.setdefault("shift_location", "")
    try:
        return template.subject.format(**values), template.body.format(**values)
    except KeyError as exc:
        raise MessagingError("missing_fields", f"template {template_id} missing {exc.args[0]}") from exc


# ---------------------------------------------------------------------------
# allowlist
# ---------------------------------------------------------------------------


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""


def allowlisted(email: str) -> bool:
    allow = get_settings().email_allowlist_domains
    if not allow:
        return False
    return domain_of(email) in allow


def assert_allowlisted(email: str) -> None:
    if not allowlisted(email):
        raise MessagingError(
            "recipient_not_allowlisted",
            f"{email} is outside the configured test-recipient allowlist",
        )


# ---------------------------------------------------------------------------
# transports
# ---------------------------------------------------------------------------


class Transport(Protocol):
    name: str

    def send(self, to_email: str, subject: str, body: str) -> str: ...


class FakeTransport:
    """Default transport. Delivers into Relay's own test inbox page.

    Judges can see the exact bytes a volunteer would receive without Relay ever
    touching a real mail server.
    """

    name = "fake"

    def send(self, to_email: str, subject: str, body: str) -> str:
        if faults.consume("delivery_error"):
            raise MessagingError("transport_error", "injected transport failure")
        if faults.consume("delivery_unknown"):
            raise DeliveryUnknown("injected indeterminate delivery result")
        return f"fake-{uuid.uuid4().hex[:16]}"


class SmtpTransport:
    """Real delivery. Off unless RELAY_EMAIL_TRANSPORT=smtp and credentials are set."""

    name = "smtp"

    def send(self, to_email: str, subject: str, body: str) -> str:
        settings = get_settings()
        if not settings.smtp_host:
            raise MessagingError("transport_unconfigured", "RELAY_SMTP_HOST is not set")

        message = EmailMessage()
        message["From"] = settings.email_from
        message["To"] = to_email
        message["Subject"] = subject
        message["Auto-Submitted"] = "auto-generated"
        message_id = f"<{uuid.uuid4().hex}@relay>"
        message["Message-ID"] = message_id
        message.set_content(body)

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as client:
                if settings.smtp_starttls:
                    client.starttls(context=ssl.create_default_context())
                if settings.smtp_user:
                    client.login(settings.smtp_user, settings.smtp_password)
                client.send_message(message)
        except smtplib.SMTPResponseException as exc:
            # 4xx and connection-phase failures leave delivery genuinely unknown.
            if 400 <= exc.smtp_code < 500:
                raise DeliveryUnknown(str(exc)) from exc
            raise MessagingError("transport_error", str(exc)) from exc
        except (TimeoutError, OSError) as exc:
            raise DeliveryUnknown(str(exc)) from exc
        return message_id


def get_transport() -> Transport:
    transport = get_settings().email_transport
    if transport == "smtp":
        return SmtpTransport()
    return FakeTransport()


# ---------------------------------------------------------------------------
# outbox
# ---------------------------------------------------------------------------


def enqueue(
    *,
    idempotency_key: str,
    to_email: str,
    subject: str,
    body: str,
    kind: str = "outreach",
    workflow_id: str | None = None,
    conn: Any = None,
) -> tuple[str, bool]:
    """Insert an outbox row. Returns (outbox_id, created).

    ``created is False`` means this exact message was already queued, which is the
    normal outcome of a duplicate event or a resumed workflow.
    """
    assert_allowlisted(to_email)
    handle = conn or store.connection()
    existing = handle.execute(
        "SELECT id FROM outbox WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    if existing is not None:
        return existing["id"], False

    outbox_id = ids.new_id("out")
    handle.execute(
        """
        INSERT INTO outbox (id, workflow_id, idempotency_key, to_email, subject, body, kind,
                            status, attempts, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)
        """,
        (outbox_id, workflow_id, idempotency_key, to_email, subject, body, kind, clock.now_iso()),
    )
    return outbox_id, True


def flush(limit: int = 25) -> dict[str, int]:
    """Send pending outbox rows.

    An indeterminate result is recorded as ``unknown`` and left alone. Relay does not
    resend a message it cannot prove was undelivered; the workflow escalates instead.
    """
    counts = {"sent": 0, "failed": 0, "unknown": 0}
    pending = store.query(
        "SELECT * FROM outbox WHERE status = 'pending' ORDER BY created_at LIMIT ?", (limit,)
    )
    transport = get_transport()

    for row in pending:
        claimed = store.execute(
            "UPDATE outbox SET attempts = attempts + 1 WHERE id = ? AND status = 'pending'",
            (row["id"],),
        )
        if claimed.rowcount == 0:
            continue

        try:
            assert_allowlisted(row["to_email"])
            provider_id = transport.send(row["to_email"], row["subject"], row["body"])
        except DeliveryUnknown as exc:
            store.execute(
                "UPDATE outbox SET status = 'unknown', error = ? WHERE id = ?",
                (str(exc)[:500], row["id"]),
            )
            counts["unknown"] += 1
            audit.record(
                "email.delivery_unknown",
                actor="system",
                outcome="error",
                workflow_id=row["workflow_id"],
                detail={"outbox_id": row["id"], "to": audit.redact(row["to_email"]), "error": str(exc)[:200]},
            )
        except Exception as exc:  # noqa: BLE001 - transports raise many shapes
            store.execute(
                "UPDATE outbox SET status = 'failed', error = ? WHERE id = ?",
                (str(exc)[:500], row["id"]),
            )
            counts["failed"] += 1
            audit.record(
                "email.failed",
                actor="system",
                outcome="error",
                workflow_id=row["workflow_id"],
                detail={"outbox_id": row["id"], "to": audit.redact(row["to_email"]), "error": str(exc)[:200]},
            )
        else:
            store.execute(
                "UPDATE outbox SET status = 'sent', provider_message_id = ?, sent_at = ? WHERE id = ?",
                (provider_id, clock.now_iso(), row["id"]),
            )
            counts["sent"] += 1
            audit.record(
                "email.sent",
                actor="system",
                workflow_id=row["workflow_id"],
                detail={
                    "outbox_id": row["id"],
                    "to": audit.redact(row["to_email"]),
                    "subject": row["subject"],
                    "transport": transport.name,
                    "provider_message_id": provider_id,
                },
            )
    return counts


def inbox(limit: int = 100) -> list[dict[str, Any]]:
    """Everything Relay has queued or sent, newest first. Powers the test inbox page."""
    rows = store.query("SELECT * FROM outbox ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))
    return store.rows_to_dicts(rows)
