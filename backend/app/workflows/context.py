"""Carried state and policies for the workflow_v1 runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.session.turn_context import ScreenTurnContext

WORKFLOW_STATE_KEY = "workflow_state"


class ToolPolicy(BaseModel):
    """Node-level capability surface declared by the pack."""

    conversation_allow: list[str] = Field(default_factory=list)

    @field_validator("conversation_allow", mode="before")
    @classmethod
    def _default_null_allow(cls, value: Any) -> Any:
        return [] if value is None else value


class StopPolicy(BaseModel):
    """Hard budget for one frame run of the workflow runtime."""

    max_llm_calls: int | None = None
    max_tool_calls: int | None = None


@dataclass
class WorkflowRuntimeContext:
    """Mutable per-frame state threaded through the engine action loop.

    Persisted inside the AgentLoop checkpoint under ``workflow_state`` so a
    frame resumed in a later turn still sees earlier ``bind``/tool outputs.
    The state is scoped to one (skill_id, task_frame_id) pair: a second SOP in
    the same session must never inherit the previous frame's bind/outputs, and
    legacy checkpoints without the scoping keys load as an empty context.
    """

    skill_id: str = ""
    task_frame_id: str = ""
    turn_context: ScreenTurnContext | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    bind: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    llm_calls: int = 0
    tool_calls: int = 0

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: dict[str, Any] | None,
        *,
        skill_id: str,
        task_frame_id: str,
        turn_context: ScreenTurnContext | None = None,
        slots: dict[str, Any] | None = None,
    ) -> WorkflowRuntimeContext:
        fresh = cls(
            skill_id=skill_id,
            task_frame_id=task_frame_id,
            turn_context=turn_context,
            slots=dict(slots or {}),
        )
        raw = dict(checkpoint or {}).get(WORKFLOW_STATE_KEY)
        state = raw if isinstance(raw, dict) else {}
        stored_skill_id = str(state.get("skill_id") or "").strip()
        stored_frame_id = str(state.get("task_frame_id") or "").strip()
        if (
            not stored_skill_id
            or not stored_frame_id
            or stored_skill_id != str(skill_id).strip()
            or stored_frame_id != str(task_frame_id).strip()
        ):
            return fresh
        outputs = state.get("outputs") if isinstance(state.get("outputs"), dict) else {}
        bind = state.get("bind") if isinstance(state.get("bind"), dict) else {}

        def _int(key: str) -> int:
            value = state.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else 0

        return cls(
            skill_id=skill_id,
            task_frame_id=task_frame_id,
            turn_context=turn_context,
            slots=dict(slots or {}),
            bind=dict(bind),
            outputs=dict(outputs),
            llm_calls=_int("llm_calls"),
            tool_calls=_int("tool_calls"),
        )

    def to_checkpoint(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "task_frame_id": self.task_frame_id,
            "bind": dict(self.bind),
            "outputs": dict(self.outputs),
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
        }

    def scopes_runtime(self) -> dict[str, Any]:
        """The ``$runtime.*`` namespace: turn context fields by name."""

        context = self.turn_context
        if context is None:
            return {}
        return {
            "project_id": context.project_id,
            "screen_id": context.screen_id,
            "screen_name": context.screen_name,
            "revision": context.revision,
            "component_count": context.component_count,
            "selected": [
                {
                    "component_id": item.component_id,
                    "name": item.name,
                    "component_type": item.component_type,
                }
                for item in context.selected
            ],
            "editor_dirty": context.editor_dirty,
        }
