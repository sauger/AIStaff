from __future__ import annotations

from typing import Literal

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
