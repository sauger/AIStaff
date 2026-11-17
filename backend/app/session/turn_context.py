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
    # 组件名，仅当用户原话点名（"把部门合同额改成饼图"）时填写；
    # 不从 selected 推断补写。空口点名不是选中。
    component: str | None = None
    dataset: str | None = None
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    charts: list[str] = Field(default_factory=list)
    time_range: str | None = None
    missing: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    reason: str = ""


class ClarificationAnswer(BaseModel):
    """One structured answer to a plan clarification (ADR-0009 / screen/v1).

    The ai-screen service validates the answer against the active task and its
    pending plan, then forwards this normalized payload. ``optionIds`` are
    server-generated and must be treated as opaque; ``draftPatch`` is the
    fragment to merge. ``planDraft`` is the full current plan when the server
    sends one, and it wins over the session copy. ``planDraftHash`` is recorded
    for the caller and is not a compare-and-swap token.
    """

    planId: str | None = None
    questionId: str = ""
    optionIds: list[str] = Field(default_factory=list)
    text: str = ""
    labels: list[str] = Field(default_factory=list)
    datasetVersionIds: list[str] = Field(default_factory=list)
    draftPatch: dict[str, Any] = Field(default_factory=dict)
    suggestedOptionIds: list[str] = Field(default_factory=list)
    planDraft: dict[str, Any] | None = None
    planDraftHash: str | None = None


class BindingSummary(BaseModel):
    """Lightweight data-binding summary of one component.

    Only what intent routing and field-mapping questions need: which dataset
    version feeds the component and which dimension/measure fields it uses.
    """

    datasetVersionId: str
    dimensions: list[str] = Field(default_factory=list)
    measures: list[dict[str, Any]] = Field(default_factory=list)


class ComponentSummary(BaseModel):
    """One screen component listed for name resolution and real options."""

    component_id: str
    name: str | None = None
    component_type: str | None = None
    locked: bool = False


class SelectedComponent(ComponentSummary):
    """One user-selected screen component, without props/style/frame payloads."""

    binding: BindingSummary | None = None


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
    # Primary selection = the last component the user selected; None when
    # nothing is selected or an old client does not send the field.
    primary_component_id: str | None = None
    components: list[ComponentSummary] = Field(default_factory=list)
    selected: list[SelectedComponent] = Field(default_factory=list)
    editor_dirty: bool = False
    plan_id: str | None = None
    plan_status: str | None = None
    # Compact active-plan projection for intent understanding. The full draft
    # stays in ai-screen; this is only enough to keep the conversation anchored.
    plan_goal: str | None = None
    plan_slots: list[str] = Field(default_factory=list)
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
    # ADR-0009: present only on a turn that answers a plan clarification.
    # Non-clarification turns omit it or send null; old senders keep working.
    clarification_answer: ClarificationAnswer | None = None

