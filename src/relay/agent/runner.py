"""Build and run the Strands agent for one coverage gap."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from strands import Agent

from .. import audit, store
from ..config import get_settings
from . import prompts
from .guardrails import RelayGuardrails
from .offline_model import PROVIDER_NAME as OFFLINE_PROVIDER, OfflineModel
from .tools import build_tools

logger = logging.getLogger(__name__)


@dataclass
class AgentRun:
    workflow_id: str
    provider: str
    model_id: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    final_text: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def tool_names(self) -> list[str]:
        return [call["tool"] for call in self.tool_calls]


def _bedrock_available() -> tuple[bool, str]:
    try:
        import boto3  # noqa: PLC0415 - optional at runtime
    except ImportError:
        return False, "boto3 is not installed"
    try:
        credentials = boto3.Session().get_credentials()
    except Exception as exc:  # noqa: BLE001 - any resolution failure means "not available"
        return False, f"credential lookup failed: {exc}"
    if credentials is None:
        return False, "no AWS credentials resolved"
    return True, "credentials resolved"


def resolve_model() -> tuple[Any, str, str]:
    """Return (model, provider_label, model_id).

    ``auto`` prefers Bedrock and silently falls back to the offline planner, because a
    judge without an AWS account should still see the product work. ``bedrock`` never
    falls back, so a real run cannot be mistaken for a simulated one.
    """
    settings = get_settings()
    choice = settings.model_provider

    if choice == "offline":
        return OfflineModel(), OFFLINE_PROVIDER, OFFLINE_PROVIDER

    available, why = _bedrock_available()
    if choice == "bedrock" and not available:
        raise RuntimeError(f"RELAY_MODEL_PROVIDER=bedrock but Bedrock is unavailable: {why}")

    if available:
        from strands.models import BedrockModel  # noqa: PLC0415 - imported only when used

        model = BedrockModel(model_id=settings.model_id, region_name=settings.aws_region)
        return model, f"bedrock:{settings.model_id}", settings.model_id

    logger.info("Falling back to the offline planner: %s", why)
    return OfflineModel(), OFFLINE_PROVIDER, OFFLINE_PROVIDER


def run_agent(workflow_id: str, *, situation: str | None = None) -> AgentRun:
    """Run one agent turn-loop against a coverage gap.

    Any failure inside the agent leaves the gap open and recorded. Relay never reports
    a workflow as finished because the model stopped talking.
    """
    try:
        model, provider, model_id = resolve_model()
    except Exception as exc:  # noqa: BLE001 - surfaced to the coordinator, not swallowed
        audit.record(
            "agent.unavailable",
            actor="system",
            outcome="error",
            workflow_id=workflow_id,
            detail={"error": str(exc)[:300]},
        )
        return AgentRun(workflow_id=workflow_id, provider="unavailable", model_id="", error=str(exc))

    store.execute("UPDATE workflows SET model_provider = ? WHERE id = ?", (provider, workflow_id))
    guardrails = RelayGuardrails(workflow_id)
    agent = Agent(
        model=model,
        tools=build_tools(workflow_id),
        system_prompt=prompts.SYSTEM_PROMPT,
        hooks=[guardrails],
        callback_handler=None,
        name="relay",
        description="Volunteer shift-recovery agent",
    )

    message = (
        prompts.resume_message(workflow_id, situation)
        if situation
        else prompts.opening_message(workflow_id)
    )
    audit.record(
        "agent.run_started",
        actor="agent",
        workflow_id=workflow_id,
        detail={"provider": provider, "situation": situation or "initial"},
    )

    try:
        result = agent(message)
        final_text = str(result).strip()
    except Exception as exc:  # noqa: BLE001 - a model or transport failure is recoverable
        audit.record(
            "agent.run_failed",
            actor="system",
            outcome="error",
            workflow_id=workflow_id,
            detail={"provider": provider, "error": str(exc)[:400], "tools_run": guardrails.tool_calls and [c["tool"] for c in guardrails.tool_calls]},
        )
        return AgentRun(
            workflow_id=workflow_id,
            provider=provider,
            model_id=model_id,
            tool_calls=guardrails.tool_calls,
            error=str(exc),
        )

    audit.record(
        "agent.run_finished",
        actor="agent",
        workflow_id=workflow_id,
        detail={
            "provider": provider,
            "tools": [call["tool"] for call in guardrails.tool_calls],
            "model_calls": guardrails.model_calls,
        },
    )
    return AgentRun(
        workflow_id=workflow_id,
        provider=provider,
        model_id=model_id,
        tool_calls=guardrails.tool_calls,
        final_text=final_text,
    )
