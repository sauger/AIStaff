from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ScreenIntentKind = Literal[
    "create",
    "modify_selected",
    "modify_screen",
    "query",
    "confirm",
    "cancel",
    "help",
]


class ScreenIntent(BaseModel):
    """Structured, non-writing interpretation of one screen-related turn."""

    kind: ScreenIntentKind = "help"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    target: Literal["current", "new", "unspecified"] = "unspecified"
    # True only when THIS utterance is an explicit placement choice
    # ("新建一张/在当前屏改"). "帮我生成一个" is not a placement confirmation.
    placement_confirmed: bool = False
    # True when this turn starts a new generation task (second generation on
    # the same screen, explicit new screen, or a confirmed new generation).
    new_generation: bool = False
    dataset: str | None = None
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    charts: list[str] = Field(default_factory=list)
    time_range: str | None = None
    missing: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    reason: str = ""


class SelectedComponent(BaseModel):
    """One user-selected screen component, without props/style/frame payloads."""

    component_id: str
    name: str | None = None
    component_type: str | None = None


class ScreenTurnContext(BaseModel):
    """Structured editor context for one chat turn (schema ``screen/v1``).

    Sent by an editing surface (e.g. the ai-screen assistant proxy) alongside
    the user's message. Unknown ``schema_id`` values are rejected by validation
    (HTTP 422 at the API boundary) instead of being silently ignored, so a
    newer editor never degrades into prompt-chip guessing.
    """

    schema_id: Literal["screen/v1"] = "screen/v1"
    project_id: str | None = None
    screen_id: str | None = None
    screen_name: str | None = None
    revision: int | None = None
    component_count: int | None = None
    blank: bool | None = None
    selected: list[SelectedComponent] = Field(default_factory=list)
    editor_dirty: bool = False
    plan_id: str | None = None
    plan_status: str | None = None
    confirm_plan: bool = False
    session_id: str | None = None
    intent: ScreenIntent | None = None
    # --- task session fields (ADR-0008): all optional, old senders keep working
    task_id: str | None = None
    task_status: str | None = None
    task_title: str | None = None
    task_brief: dict[str, Any] | None = None
    placement: str | None = None
    workspace: dict[str, Any] | None = None
    entry: str | None = None

