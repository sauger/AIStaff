"""Per-operation LLM call budgets.

The model round trip dominates an interactive turn. Measured on the local
database: ``harness.task_action`` had a p50 of 11.6s against an 8192-token
output ceiling, and 96 of 873 calls spent more than 60 seconds turning
provider-default reasoning into a small action JSON envelope.

These policies bound each stage by output tokens, wall time and thinking mode
so one slow call cannot stretch a whole turn to minutes. Unknown operations
return ``None`` and keep the model configuration's behaviour unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CallPolicy:
    max_tokens: int
    timeout_seconds: float | None = None
    thinking: str = ""
    retries: int = 0


CALL_POLICIES: dict[str, CallPolicy] = {
    "harness.task_action": CallPolicy(1600, 45.0, "disabled"),
    "harness.plan_compile": CallPolicy(1200, 45.0, "disabled"),
    "plan.replan": CallPolicy(1000, 45.0, "disabled"),
    "plan.synthesize": CallPolicy(1200, 45.0, "disabled"),
    # The planner returns a small JSON document, but the SOP task frame can
    # include all requirements. A 300-token cap truncates it mid-JSON and
    # causes JSON repair to fail. Keep a bounded but sufficient budget.
    "turn_planner.plan": CallPolicy(
        int(os.getenv("TURN_PLANNER_MAX_TOKENS", "1200")),
        float(os.getenv("TURN_PLANNER_TIMEOUT_SECONDS", "20")),
        "disabled",
    ),
    # Workflow structured steps (plan_change, ...) produce a small JSON object.
    # Without a policy they inherited the provider default and could spend 90s+
    # thinking before emitting 6 components.
    "workflow.structured_step": CallPolicy(1200, 30.0, "disabled"),
    "knowledge.document_route": CallPolicy(600, 30.0, "disabled"),
    "knowledge.bucket_route": CallPolicy(600, 30.0, "disabled"),
    "memory.capture": CallPolicy(400, 30.0, "disabled"),
    "session.title": CallPolicy(400, 30.0, "disabled"),
}


def policy_for(operation: str) -> CallPolicy | None:
    """Return the budget for ``operation``; ``None`` keeps the model default."""

    return CALL_POLICIES.get(str(operation or "").strip())
