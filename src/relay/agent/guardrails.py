"""Lifecycle hooks around the agent's tool use.

Strands hooks give Relay a place to observe and stop tool calls from outside the tool
itself. That matters for two reasons:

* the trace shown to a coordinator is built from what actually ran, not from what the
  model said it would do;
* the model-call and tool-call budgets are enforced even if a tool is somehow
  reachable without its own accounting.

This is defence in depth, not the boundary. The boundary is inside each operation.
"""

from __future__ import annotations

import json
from typing import Any

from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from .. import audit, store
from ..config import get_settings

MAX_LOGGED_RESULT_CHARS = 4000


class RelayGuardrails(HookProvider):
    """Per-run accounting, budget enforcement, and the observable action trace."""

    def __init__(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id
        self.tool_calls: list[dict[str, Any]] = []
        self.model_calls = 0
        self.denied: list[dict[str, Any]] = []

    # -- registration -------------------------------------------------------

    def register_hooks(self, registry: HookRegistry, **_: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterModelCallEvent, self.after_model)
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    # -- model --------------------------------------------------------------

    def before_model(self, event: BeforeModelCallEvent) -> None:
        self.model_calls += 1
        store.execute(
            "UPDATE workflows SET model_calls = model_calls + 1 WHERE id = ?", (self.workflow_id,)
        )

    def after_model(self, event: AfterModelCallEvent) -> None:
        if getattr(event, "exception", None) is not None:
            audit.record(
                "model.error",
                actor="system",
                outcome="error",
                workflow_id=self.workflow_id,
                detail={"error": str(event.exception)[:300]},
            )

    # -- tools --------------------------------------------------------------

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use.get("name", "unknown")
        limit = get_settings().max_tool_calls_per_workflow
        if len(self.tool_calls) >= limit:
            event.cancel_tool = (
                f"Relay's per-gap tool budget of {limit} calls is used up. Stop and escalate."
            )
            self.denied.append({"tool": name, "reason": "tool_budget_exceeded"})
            audit.record(
                "hook.tool_cancelled",
                actor="system",
                outcome="denied",
                workflow_id=self.workflow_id,
                detail={"tool": name, "reason": "tool_budget_exceeded", "limit": limit},
            )

    def after_tool(self, event: AfterToolCallEvent) -> None:
        name = event.tool_use.get("name", "unknown")
        payload = _result_payload(event.result)
        entry = {
            "tool": name,
            "input": event.tool_use.get("input", {}),
            "ok": payload.get("ok", True) if isinstance(payload, dict) else True,
            "status": event.result.get("status") if isinstance(event.result, dict) else "unknown",
            "result": payload,
        }
        self.tool_calls.append(entry)
        audit.record(
            "agent.tool_result",
            actor="agent",
            outcome="ok" if entry["ok"] else "denied",
            workflow_id=self.workflow_id,
            detail={
                "tool": name,
                "input": entry["input"],
                "status": entry["status"],
                "error": payload.get("error") if isinstance(payload, dict) else None,
            },
        )


def _result_payload(result: Any) -> Any:
    """Pull the JSON body out of a Strands ToolResult without assuming its shape."""
    if not isinstance(result, dict):
        return {"raw": str(result)[:MAX_LOGGED_RESULT_CHARS]}
    for block in result.get("content", []) or []:
        if isinstance(block, dict) and "json" in block:
            return block["json"]
        if isinstance(block, dict) and "text" in block:
            text = block["text"]
            try:
                return json.loads(text)
            except (TypeError, ValueError):
                return {"text": str(text)[:MAX_LOGGED_RESULT_CHARS]}
    return {"status": result.get("status", "unknown")}
