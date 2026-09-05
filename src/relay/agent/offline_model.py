"""A deterministic Strands model provider that needs no network and no credentials.

Why this exists: a judge should be able to clone the repository and watch the whole
recovery loop run, on a laptop, with no AWS account. This provider implements the
Strands ``Model`` interface and drives the same agent, the same tools and the same
enforcement path that Bedrock drives. What it does *not* do is reason -- it applies a
small, readable planner.

Relay never hides which one ran. Every workflow row stores its model provider, the
receipt prints it, and the UI labels it. Results produced under
``offline-deterministic`` are not evidence about a language model's behaviour.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any

from strands.models import Model
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

from .. import faults

PROVIDER_NAME = "offline-deterministic"

# Words that suggest a volunteer's roster note is relevant to a particular shift.
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "has", "have", "her", "his",
    "they", "them", "will", "can", "but", "not", "are", "was", "were", "been", "than",
    "then", "when", "who", "how", "any", "all", "one", "two", "shift", "volunteer",
    "volunteers", "line", "other", "next", "week", "month", "day", "days", "time",
}
_WORD = re.compile(r"[a-z][a-z0-9_]{2,}")

_ALTERNATIVE_HINT = re.compile(
    r"(?i)\b(could|can|able to|happy to|available)\b[^.?!]{0,80}\b(instead|later|afternoon|morning|evening|tomorrow|another)\b"
)


class OfflineModel(Model):
    """Plans the coverage loop from tool results already in the message history."""

    def __init__(self, **config: Any) -> None:
        self._config: dict[str, Any] = {"model_id": PROVIDER_NAME, **config}

    # -- Model interface ----------------------------------------------------

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return dict(self._config)

    async def structured_output(self, output_model, prompt, **kwargs) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError(
            "The offline planner does not produce structured output. "
            "Set RELAY_MODEL_PROVIDER=bedrock for that."
        )

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        if faults.consume("model_error"):
            raise RuntimeError("injected model failure")

        available = {spec["name"] for spec in (tool_specs or [])}
        seen = _tool_results(messages)
        decision = _decide(seen, available)

        if decision["kind"] == "tool":
            async for event in _emit_tool_use(decision["name"], decision["input"]):
                yield event
        else:
            async for event in _emit_text(decision["text"]):
                yield event


# ---------------------------------------------------------------------------
# reading the conversation
# ---------------------------------------------------------------------------


def _tool_results(messages: Messages) -> dict[str, Any]:
    """Map tool name -> most recent parsed result body."""
    names: dict[str, str] = {}
    results: dict[str, Any] = {}
    for message in messages:
        for block in message.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if "toolUse" in block:
                use = block["toolUse"]
                names[use.get("toolUseId", "")] = use.get("name", "")
            elif "toolResult" in block:
                result = block["toolResult"]
                name = names.get(result.get("toolUseId", ""), "")
                if not name:
                    continue
                results[name] = _parse_result(result)
    return results


def _parse_result(result: dict[str, Any]) -> Any:
    for block in result.get("content", []) or []:
        if isinstance(block, dict) and "json" in block:
            return block["json"]
        if isinstance(block, dict) and "text" in block:
            try:
                return json.loads(block["text"])
            except (TypeError, ValueError):
                return {"text": block["text"]}
    return {"status": result.get("status", "unknown")}


# ---------------------------------------------------------------------------
# the planner
# ---------------------------------------------------------------------------


def _decide(seen: dict[str, Any], available: set[str]) -> dict[str, Any]:
    if "load_shift_context" in available and "load_shift_context" not in seen:
        return {"kind": "tool", "name": "load_shift_context", "input": {}}

    context = seen.get("load_shift_context") or {}
    if "eligible_volunteers" in available and "eligible_volunteers" not in seen:
        return {"kind": "tool", "name": "eligible_volunteers", "input": {}}

    candidates = seen.get("eligible_volunteers") or {}
    contacted = seen.get("request_coverage")
    escalated = seen.get("escalate_gap")

    if contacted is None and escalated is None:
        eligible = candidates.get("eligible") or []
        if eligible:
            return _plan_outreach(context, candidates)
        return _plan_escalation(context, candidates, reason="no_eligible_volunteer")

    if contacted is not None and not contacted.get("ok", False) and escalated is None:
        return _plan_escalation(
            context,
            candidates,
            reason=contacted.get("error", "outreach_refused"),
            detail=contacted.get("message", ""),
        )

    if "write_receipt" in available and "write_receipt" not in seen:
        return _plan_receipt(context, candidates, contacted, escalated)

    return {"kind": "text", "text": _closing_text(contacted, escalated)}


def _shift_keywords(context: dict[str, Any]) -> set[str]:
    shift = context.get("shift") or {}
    text = " ".join(str(shift.get(key, "")) for key in ("title", "notes", "location")).lower()
    return {word for word in _WORD.findall(text) if word not in _STOPWORDS}


def _rank(context: dict[str, Any], eligible: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order candidates by note relevance, then by who has been asked least lately."""
    keywords = _shift_keywords(context)

    def score(candidate: dict[str, Any]) -> tuple[int, int, str]:
        note_words = {
            word for word in _WORD.findall(str(candidate.get("notes", "")).lower())
            if word not in _STOPWORDS
        }
        overlap = len(keywords & note_words)
        asked = int(candidate.get("requests_last_7_days", 0))
        return (-overlap, asked, candidate.get("name", ""))

    return sorted(eligible, key=score)


def _plan_outreach(context: dict[str, Any], candidates: dict[str, Any]) -> dict[str, Any]:
    ranked = _rank(context, candidates.get("eligible") or [])
    wave_size = int((context.get("policy") or {}).get("wave_size", 2))
    chosen = ranked[:wave_size]
    shift = context.get("shift") or {}

    reasons = []
    keywords = _shift_keywords(context)
    for candidate in chosen:
        note_words = {
            word for word in _WORD.findall(str(candidate.get("notes", "")).lower())
            if word not in _STOPWORDS
        }
        shared = sorted(keywords & note_words)
        if shared:
            reasons.append(f"{candidate['name']} (roster note mentions {', '.join(shared[:2])})")
        else:
            reasons.append(f"{candidate['name']} (eligible, asked {candidate.get('requests_last_7_days', 0)}x this week)")

    rationale = (
        f"Asking {len(chosen)} of {len(ranked)} eligible volunteers first: " + "; ".join(reasons) + "."
    )
    personal_note = (
        f"It is the {shift.get('title', 'shift').lower()} and we are one pair of hands short."
    )
    return {
        "kind": "tool",
        "name": "request_coverage",
        "input": {
            "volunteer_ids": [candidate["volunteer_id"] for candidate in chosen],
            "rationale": rationale,
            "personal_note": personal_note,
        },
    }


def _blocker_counts(candidates: dict[str, Any]) -> dict[str, int]:
    blockers: dict[str, int] = {}
    for exclusion in candidates.get("excluded") or []:
        for code in exclusion.get("codes", []):
            blockers[code] = blockers.get(code, 0) + 1
    return blockers


def _plan_escalation(
    context: dict[str, Any],
    candidates: dict[str, Any],
    *,
    reason: str,
    detail: str = "",
) -> dict[str, Any]:
    shift = context.get("shift") or {}
    cancellation = context.get("cancellation") or {}
    excluded = candidates.get("excluded") or []
    required = shift.get("required_certification") or ""

    blockers = _blocker_counts(candidates)
    breakdown = ", ".join(f"{count} {code.replace('_', ' ')}" for code, count in sorted(blockers.items()))
    asked = blockers.get("already_contacted", 0)
    # An exclusion counts as "certification" only if nothing else already ruled the
    # person out, otherwise every escalation would blame the certificate.
    cert_only = sum(
        1 for exclusion in excluded if exclusion.get("codes") == ["missing_certification"]
    )

    if reason != "no_eligible_volunteer":
        question = f"Relay could not complete this automatically ({reason}). How would you like to proceed?"
    elif asked:
        question = (
            f"I have asked all {asked} volunteer(s) the rules allow for this slot and none of them "
            "accepted in time. Do you want to assign someone yourself, or leave the slot open?"
        )
    elif required and cert_only:
        question = (
            f"Nobody else on the roster holds the '{required}' sign-off this shift requires. "
            "Do you want to cover it another way, or leave the slot open?"
        )
    else:
        question = "Nobody on the roster is contactable for this slot right now. How would you like to proceed?"

    total_records = len(excluded) + len(candidates.get("eligible") or [])
    summary = (
        f"{cancellation.get('volunteer_name', 'A volunteer')} cancelled {shift.get('title', 'the shift')} "
        f"({shift.get('when_human', '')}). I checked {total_records} roster records against the "
        f"organisation's rules: {breakdown}. "
    )
    summary += (
        f"I contacted {asked} volunteer(s) and none accepted before the deadline. "
        if asked
        else "There was nobody I was allowed to contact, so no request was sent. "
    )
    summary += "The roster is unchanged and the slot is still open."
    if detail:
        summary += f" Tool response: {detail}"
    note = cancellation.get("note_from_volunteer") or ""
    if _looks_like_injection(note):
        summary += (
            " Note: the cancellation message contains text that reads like an instruction to me. "
            "I treated it as data and ignored it; you may want to look at it."
        )
    elif _ALTERNATIVE_HINT.search(note):
        summary += f" The volunteer's note offers an alternative: \"{note.strip()[:160]}\""

    return {
        "kind": "tool",
        "name": "escalate_gap",
        "input": {
            "question": question,
            "blocker": reason,
            "summary": summary,
            "options": ["assign_specific_volunteer", "mark_unresolved", "correct_source_data"],
        },
    }


def _plan_receipt(
    context: dict[str, Any],
    candidates: dict[str, Any],
    contacted: dict[str, Any] | None,
    escalated: dict[str, Any] | None,
) -> dict[str, Any]:
    shift = (context.get("shift") or {})
    if contacted and contacted.get("ok"):
        names = ", ".join(item.get("name", item.get("volunteer_id", "")) for item in contacted.get("contacted", []))
        excluded = candidates.get("excluded") or []
        summary = (
            f"Asked {names} to cover {shift.get('title', 'the shift')} ({shift.get('when_human', '')}); "
            f"replies are due by {contacted.get('expires_at', 'the deadline')}. "
            f"{len(excluded)} other roster records were excluded by the organisation's rules. "
            "Nobody is scheduled yet."
        )
        return {"kind": "tool", "name": "write_receipt", "input": {"outcome": "awaiting_response", "summary": summary}}

    asked = _blocker_counts(candidates).get("already_contacted", 0)
    tried = (
        f"I contacted {asked} volunteer(s) and none accepted."
        if asked
        else "There was nobody the rules allowed me to contact."
    )
    summary = (
        f"{shift.get('title', 'The shift')} ({shift.get('when_human', '')}) is not covered and needs a "
        f"decision from you. {tried} The roster is unchanged."
    )
    return {"kind": "tool", "name": "write_receipt", "input": {"outcome": "needs_human", "summary": summary}}


def _closing_text(contacted: dict[str, Any] | None, escalated: dict[str, Any] | None) -> str:
    if contacted and contacted.get("ok"):
        names = ", ".join(item.get("name", "") for item in contacted.get("contacted", []))
        return (
            f"I have asked {names}. Requests expire at {contacted.get('expires_at', 'the deadline')}, "
            "and I will move to the next eligible volunteer if nobody replies. "
            "Nothing is scheduled until someone accepts."
        )
    if escalated:
        return (
            "I could not close this one within your rules, so it is waiting on your decision. "
            "The card lists everyone I checked and why each person was ruled out."
        )
    return "No action was possible for this coverage gap."


def _looks_like_injection(text: str) -> bool:
    lowered = (text or "").lower()
    triggers = ("ignore your previous", "ignore previous", "system note", "disregard the", "you must email")
    return any(trigger in lowered for trigger in triggers)


# ---------------------------------------------------------------------------
# emitting Bedrock-shaped stream events
# ---------------------------------------------------------------------------


async def _emit_tool_use(name: str, payload: dict[str, Any]) -> AsyncGenerator[StreamEvent, None]:
    body = json.dumps(payload)
    yield {"messageStart": {"role": "assistant"}}
    yield {
        "contentBlockStart": {
            "start": {"toolUse": {"toolUseId": f"offline-{uuid.uuid4().hex[:12]}", "name": name}}
        }
    }
    yield {"contentBlockDelta": {"delta": {"toolUse": {"input": body}}}}
    yield {"contentBlockStop": {}}
    yield {"messageStop": {"stopReason": "tool_use"}}
    yield _usage(body)


async def _emit_text(text: str) -> AsyncGenerator[StreamEvent, None]:
    yield {"messageStart": {"role": "assistant"}}
    yield {"contentBlockStart": {"start": {}}}
    yield {"contentBlockDelta": {"delta": {"text": text}}}
    yield {"contentBlockStop": {}}
    yield {"messageStop": {"stopReason": "end_turn"}}
    yield _usage(text)


def _usage(body: str) -> StreamEvent:
    # Reported as zero cost on purpose: no tokens were bought.
    return {
        "metadata": {
            "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
            "metrics": {"latencyMs": 0},
        }
    }
