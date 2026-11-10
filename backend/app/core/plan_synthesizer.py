"""Answer synthesis for compiled execution.

The planner decides *what* to call; this module writes the final reply once the
tools have run. It costs one small model call and replaces the ReAct-style
post-hoc reasoning that would otherwise re-read the whole transcript.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.llm.client import LLMClient
from app.observability.spans import llm_operation

_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "llm" / "prompts" / "plan_synthesizer_prompt.md"
)


class PlanSynthesizer:
    """Turn node outcomes into one user-facing reply."""

    def __init__(
        self,
        client_factory: Callable[[Any], LLMClient] = LLMClient,
        *,
        prompt_path: Path | None = None,
        max_outcome_chars: int = 1500,
    ) -> None:
        self.client_factory = client_factory
        self.prompt_path = prompt_path or _PROMPT_PATH
        self.max_outcome_chars = max(200, int(max_outcome_chars))

    def _system_prompt(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")

    def synthesize(
        self,
        *,
        requirement: Any,
        model_config: Any,
        outcomes: list[Any],
        reply_hint: str = "",
        cancellation: Any = None,
    ) -> str:
        goal = dict(getattr(requirement, "known_slots", {}) or {})
        payload = {
            "user_message": str(
                getattr(requirement, "source_user_message", "")
                or goal.get("user_request")
                or ""
            )[:2000],
            "goal": goal,
            "requirements": list(getattr(requirement, "requirements", []) or [])[:8],
            "reply_hint": reply_hint[:500],
            "outcomes": [
                {
                    "node_id": outcome.node_id,
                    "tool": outcome.tool,
                    "status": outcome.status,
                    "error": outcome.error,
                    "data": _bounded_json(
                        (outcome.result or {}).get("data"),
                        self.max_outcome_chars,
                    ),
                }
                for outcome in outcomes
            ],
        }
        try:
            with llm_operation("plan.synthesize"):
                client = self.client_factory(model_config)
                data = client.generate_json(self._system_prompt(), payload, cancellation)
        except Exception:  # noqa: BLE001 - reply falls back to the template
            return ""
        if not isinstance(data, dict):
            return ""
        reply = data.get("reply")
        return reply.strip() if isinstance(reply, str) else ""


def _bounded_json(value: Any, max_chars: int) -> Any:
    """Keep numeric detail for the synthesizer; only trim when it is huge."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)[:max_chars]
    if len(text) <= max_chars:
        return value
    return text[:max_chars] + "…（已截断）"
