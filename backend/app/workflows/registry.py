"""Static registry for workflow binders and structured-LLM step handlers.

Registration is code-only: SOP metadata may NAME a binder/handler by key, but
the key → callable mapping lives here and is populated exclusively by imports
of reviewed pack modules (see ``app.workflow_packs``). Nothing derived from
``SkillCard.metadata`` is ever imported, so a crafted SOP cannot load
arbitrary code by editing its ``binder`` field.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BindOutcome:
    """Result of one binder run.

    ``bind`` is written into ``WorkflowRuntimeContext.bind`` (reachable as
    ``$bind.*``) and also emitted as the step's ``slot_updates`` so the values
    survive into the next turn. Non-empty ``missing_fields`` turns the step
    into an awaiting-user stop with no LLM and no tool calls.
    """

    bind: dict[str, Any]
    missing_fields: list[str] = field(default_factory=list)
    question: str = ""
    next_step_id: str | None = None


class BindError(ValueError):
    """A binder cannot decide the branch from the turn context.

    Raised for missing or contradictory structured input (e.g. the editor did
    not report ``component_count``). The runtime turns it into a failed step
    with a user-visible message; it never guesses and never falls back to an
    LLM.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class StructuredStepError(ValueError):
    """A structured step cannot produce a usable plan.

    ``ask_fields`` non-empty means the failure is answerable by the user
    (e.g. ChangePlan targets outside the bound set): the runtime converts it
    to an awaiting-user stop instead of a hard failure, and never retries the
    write.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        ask_fields: list[str] | None = None,
        slot_updates: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.ask_fields = list(ask_fields or [])
        # Optional structured follow-up payload (e.g. pending_edit) persisted
        # alongside the awaiting-user stop by the runtime's slot_updates path.
        self.slot_updates: dict[str, Any] | None = dict(slot_updates) if slot_updates else None


BinderCallable = Callable[..., BindOutcome]
StructuredStepCallable = Callable[..., dict[str, Any]]

BINDERS: dict[str, BinderCallable] = {}
STRUCTURED_STEPS: dict[str, StructuredStepCallable] = {}


def register_binder(name: str) -> Callable[[BinderCallable], BinderCallable]:
    key = str(name or "").strip()
    if not key:
        raise ValueError("binder 名称不能为空。")
    if key in BINDERS:
        raise ValueError(f"binder 已注册：{key}")

    def _register(func: BinderCallable) -> BinderCallable:
        BINDERS[key] = func
        return func

    return _register


def register_structured_step(
    name: str,
) -> Callable[[StructuredStepCallable], StructuredStepCallable]:
    key = str(name or "").strip()
    if not key:
        raise ValueError("structured handler 名称不能为空。")
    if key in STRUCTURED_STEPS:
        raise ValueError(f"structured handler 已注册：{key}")

    def _register(func: StructuredStepCallable) -> StructuredStepCallable:
        STRUCTURED_STEPS[key] = func
        return func

    return _register
