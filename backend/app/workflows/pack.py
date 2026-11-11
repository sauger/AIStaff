"""Workflow pack policy loaded from ``SkillCard.metadata.workflow``."""

from __future__ import annotations

from typing import Any

from app.workflows.context import StopPolicy, ToolPolicy

RUNTIME_WORKFLOW_V1 = "workflow_v1"


class WorkflowPackError(ValueError):
    """The workflow metadata is present but invalid — fail closed, no fallback."""


class WorkflowPack:
    """Declarative execution policy for one published SOP."""

    def __init__(
        self,
        *,
        runtime: str,
        binder_ref: str | None,
        tool_policy: ToolPolicy,
        stop_policy: StopPolicy,
    ) -> None:
        self.runtime = runtime
        self.binder_ref = binder_ref
        self.tool_policy = tool_policy
        self.stop_policy = stop_policy

    @classmethod
    def from_skill(cls, skill: Any) -> WorkflowPack | None:
        """Load the pack from ``skill.content_json["metadata"]["workflow"]``.

        Returns ``None`` when the skill declares no workflow metadata at all —
        those skills keep the legacy execution path untouched. A declared
        workflow with an unknown ``runtime`` raises :class:`WorkflowPackError`
        instead of silently degrading to free planning.
        """

        content = getattr(skill, "content_json", None) or {}
        if not isinstance(content, dict):
            return None
        metadata = content.get("metadata")
        if not isinstance(metadata, dict) or "workflow" not in metadata:
            return None
        workflow = metadata.get("workflow")
        if not isinstance(workflow, dict):
            raise WorkflowPackError("metadata.workflow 必须是对象。")
        runtime = str(workflow.get("runtime") or "").strip()
        if runtime != RUNTIME_WORKFLOW_V1:
            raise WorkflowPackError(
                f"未知的 workflow runtime：{runtime or '<空>'}（当前仅支持 {RUNTIME_WORKFLOW_V1}）。"
            )
        binder_ref = str(workflow.get("binder") or "").strip() or None
        tool_policy_raw = workflow.get("tool_policy")
        stop_policy_raw = workflow.get("stop")
        return cls(
            runtime=runtime,
            binder_ref=binder_ref,
            tool_policy=ToolPolicy.model_validate(
                tool_policy_raw if isinstance(tool_policy_raw, dict) else {}
            ),
            stop_policy=StopPolicy.model_validate(
                stop_policy_raw if isinstance(stop_policy_raw, dict) else {}
            ),
        )

    def runtime_action(self, node: Any) -> str | None:
        """The declared executor for one SOP node (``node.metadata.runtime_action``)."""

        metadata = node.get("metadata") if isinstance(node, dict) else None
        if not isinstance(metadata, dict):
            return None
        action = str(metadata.get("runtime_action") or "").strip()
        return action or None
