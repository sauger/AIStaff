"""workflow_v1 step runtime: declarative node executors, zero free planning.

Executors: ``bind`` / ``tool`` / ``structured_llm`` / ``ask`` / ``reply``.
Binders and structured handlers are resolved through the static registry in
``app.workflows.registry`` (populated only by reviewed pack imports); an
unknown action or registry key fails closed instead of degrading to the
legacy LLM path.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.task_request_compiler import TaskExecutionResult
from app.llm import LLMError
from app.workflows.context import WorkflowRuntimeContext
from app.workflows.expressions import (
    ExpressionScopes,
    UnresolvedExpression,
    render_template,
    resolve_arguments,
)
from app.workflows.pack import WorkflowPack
from app.workflows.registry import (
    BINDERS,
    STRUCTURED_STEPS,
    BindError,
    BindOutcome,
    StructuredStepError,
)

TraceSink = Callable[[str, dict[str, Any]], None]
LlmGenerate = Callable[[str, dict[str, Any]], dict[str, Any]]


def _type_matches(value: Any, type_name: str) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "null":
        return value is None
    return True


def validate_tool_arguments(
    arguments: dict[str, Any],
    input_schema: dict[str, Any] | None,
) -> list[str]:
    """Check ``required`` keys and basic declared types before invoking.

    Only JSON-schema basics are enforced here; the MCP tool remains the
    authority for deeper validation.
    """

    schema = input_schema if isinstance(input_schema, dict) else {}
    errors: list[str] = []
    for key in schema.get("required") or []:
        if isinstance(key, str) and key not in arguments:
            errors.append(f"缺少必填参数 {key}")
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    for key, value in arguments.items():
        declaration = properties.get(key)
        if not isinstance(declaration, dict):
            continue
        type_name = str(declaration.get("type") or "").strip()
        if type_name and not _type_matches(value, type_name):
            errors.append(
                f"参数 {key} 需要 {type_name} 类型，实际为 {type(value).__name__}"
            )
    return errors


class WorkflowRuntime:
    """Executes one SOP node declared with ``metadata.runtime_action``."""

    def __init__(
        self,
        *,
        pack: WorkflowPack,
        invoker: Any,
        manifest: Any,
        trace: TraceSink,
        task_frame_id: str,
        skill_id: str,
        allowed_tool_refs: set[str],
        turn_context: Any = None,
        checkpoint: dict[str, Any] | None = None,
        slots: dict[str, Any] | None = None,
        llm_generate: LlmGenerate | None = None,
        source_user_message: str = "",
    ) -> None:
        self.pack = pack
        self.invoker = invoker
        self.trace = trace
        self.task_frame_id = task_frame_id
        self.skill_id = skill_id
        self.source_user_message = str(source_user_message or "")
        self._llm_generate = llm_generate
        self._base_checkpoint = dict(checkpoint or {})
        self.context = WorkflowRuntimeContext.from_checkpoint(
            checkpoint,
            skill_id=skill_id,
            task_frame_id=task_frame_id,
            turn_context=turn_context,
            slots=slots,
        )
        # 节点级工具面：只保留当前 SOP 节点 capability_refs 声明过的工具。
        # 空集合 = 该节点没有任何工具权限（fail closed），manifest 再大也不放行。
        refs = {
            str(item).strip()
            for item in (allowed_tool_refs or ())
            if str(item or "").strip()
        }
        self._descriptors: dict[str, Any] = {}
        for item in getattr(manifest, "available", []) or []:
            name = str(getattr(item, "name", "") or "")
            if not name or not getattr(item, "available", True):
                continue
            metadata = getattr(item, "metadata", None) or {}
            source_tool_name = str(metadata.get("source_tool_name") or "").strip()
            leaf = source_tool_name.rsplit(".", 1)[-1]
            if (
                str(getattr(item, "capability_id", "") or "") in refs
                or name in refs
                or source_tool_name in refs
                or leaf in refs
            ):
                self._descriptors[name] = item

    def _find_descriptor(self, tool: str) -> Any | None:
        """Resolve the declared tool name inside the filtered surface.

        A node may declare the tool by invocation name, capability id, or the
        Tool row name (``source_tool_name``); all three identities are valid
        SOP-side references, but only descriptors that passed the
        ``allowed_tool_refs`` filter can ever be returned.
        """

        for name, item in self._descriptors.items():
            metadata = getattr(item, "metadata", None) or {}
            source_tool_name = str(metadata.get("source_tool_name") or "").strip()
            if (
                tool == name
                or tool == str(getattr(item, "capability_id", "") or "")
                or tool == source_tool_name
                or tool == source_tool_name.rsplit(".", 1)[-1]
            ):
                return item
        return None

    # ------------------------------------------------------------------ API

    def run_step(
        self,
        *,
        node_id: str,
        node: dict[str, Any],
    ) -> TaskExecutionResult:
        try:
            # 静态注册表只由代码填充；直接构造 WorkflowRuntime 的调用方
            # （测试、未来的 CLI）也可能没有先 import pack 包。
            import app.workflow_packs  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            return self._failed(
                "WORKFLOW_PACK_IMPORT_FAILED",
                f"workflow pack 导入失败：{exc}",
            )
        action = self.pack.runtime_action(node)
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        self.trace(
            "workflow_step_started",
            {
                "step_id": node_id,
                "runtime_action": action,
                "llm_calls": self.context.llm_calls,
                "tool_calls": self.context.tool_calls,
            },
        )
        if action == "bind":
            result = self._run_bind_step(node_id, metadata)
        elif action == "structured_llm":
            result = self._run_structured_llm_step(node_id, metadata)
        elif action == "tool":
            result = self._run_tool_step(node_id, metadata)
        elif action == "ask":
            expected_fields = _text_list(node.get("expected_user_info"))
            if not expected_fields:
                # 没有声明要收集的字段，停点无法持久化（agent_loop 只在
                # expected_user_info 有缺字段时写 awaiting_input），问完即丢、
                # 下一轮必然重复提问——宁可不问也不允许这种静默停点。
                result = self._failed(
                    "WORKFLOW_ASK_SLOTS_MISSING",
                    f"ask 节点 {node_id} 未声明 expected_user_info，停点无法持久化，拒绝渲染问题。",
                )
            else:
                result = self._run_ask_step(node_id, node, metadata)
        elif action == "reply":
            result = self._run_reply_step(node_id, node, metadata)
        else:
            result = self._failed(
                "WORKFLOW_ACTION_UNKNOWN",
                f"未知的 runtime_action：{action or '<空>'}，节点 {node_id} 失败关闭。",
            )
        self.trace(
            "workflow_step_completed",
            {
                "step_id": node_id,
                "status": result.status,
                "tool_calls": self.context.tool_calls,
                "llm_calls": self.context.llm_calls,
                "stop_reason": (
                    str(result.error.get("stop_reason"))
                    if isinstance(result.error, dict)
                    and result.error.get("stop_reason")
                    else result.status
                ),
            },
        )
        return result

    # ------------------------------------------------------------- executors

    def _scopes(self) -> ExpressionScopes:
        return ExpressionScopes(
            runtime=self.context.scopes_runtime(),
            slot=self.context.slots,
            bind=self.context.bind,
            out=self.context.outputs,
        )

    def _next_step_id(self, metadata: dict[str, Any]) -> str | None:
        return str(metadata.get("next_step_id") or "").strip() or None

    def _run_tool_step(
        self,
        node_id: str,
        metadata: dict[str, Any],
    ) -> TaskExecutionResult:
        tool = str(metadata.get("tool") or "").strip()
        if not tool:
            return self._failed(
                "WORKFLOW_NODE_INVALID",
                f"tool 节点 {node_id} 未声明 metadata.tool。",
            )
        max_tool_calls = self.pack.stop_policy.max_tool_calls
        if max_tool_calls is not None and self.context.tool_calls >= max_tool_calls:
            return self._failed(
                "WORKFLOW_BUDGET_EXCEEDED",
                f"已达到本帧工具调用上限 {max_tool_calls}，节点 {node_id} 停止执行。",
                stop_reason="budget",
            )
        raw_arguments = metadata.get("arguments")
        if raw_arguments is None:
            raw_arguments = {}
        if not isinstance(raw_arguments, dict):
            return self._failed(
                "WORKFLOW_NODE_INVALID",
                f"tool 节点 {node_id} 的 metadata.arguments 必须是对象。",
            )
        try:
            arguments = resolve_arguments(raw_arguments, self._scopes())
        except UnresolvedExpression as exc:
            return self._failed(
                "EXPRESSION_UNRESOLVED",
                f"节点 {node_id} 参数表达式解析失败：{exc}",
            )
        descriptor = self._find_descriptor(tool)
        if descriptor is None:
            return self._failed(
                "TOOL_NOT_ALLOWED",
                f"工具 {tool} 未在当前 SOP 节点声明，不在本节点的授权工具面内。",
            )
        errors = validate_tool_arguments(arguments, descriptor.input_schema)
        if errors:
            return self._failed(
                "TOOL_ARGUMENTS_INVALID",
                f"工具 {tool} 参数校验失败：" + "；".join(errors),
            )
        # invoker 按 manifest 的 invocation name 分派；节点可以用三种身份
        # 之一声明工具，真正调用必须落到 descriptor.name 上。
        invocation_name = str(getattr(descriptor, "name", "") or "").strip() or tool
        outcome = self.invoker.invoke(invocation_name, arguments)
        self.context.tool_calls += 1
        if outcome.get("success") is not True:
            error = outcome.get("error") if isinstance(outcome.get("error"), dict) else {}
            return self._failed(
                str(error.get("code") or "WORKFLOW_TOOL_FAILED"),
                str(error.get("message") or f"工具 {tool} 调用失败。"),
            )
        data = outcome.get("data")
        if isinstance(data, dict) and data.get("kind") == "sandbox_json_file":
            try:
                data = self.invoker.read_tool_result_reference(data)
            except Exception as exc:  # noqa: BLE001 - 引用失效必须失败关闭
                return self._failed(
                    "TOOL_RESULT_REFERENCE_UNAVAILABLE",
                    f"工具 {tool} 的大结果引用无法读取：{exc}",
                )
        self.context.outputs[node_id] = {"status": "ok", "data": data}
        return self._result(
            status="completed",
            task_summary=f"工作流节点 {node_id}（tool:{tool}）执行成功。",
            capability_results=[
                {"tool_name": invocation_name, "success": True, "data": data}
            ],
            next_step_id=self._next_step_id(metadata),
        )

    def _run_bind_step(
        self,
        node_id: str,
        metadata: dict[str, Any],
    ) -> TaskExecutionResult:
        binder_key = str(metadata.get("binder") or "").strip()
        if not binder_key:
            return self._failed(
                "WORKFLOW_BINDER_UNDECLARED",
                f"bind 节点 {node_id} 未声明 metadata.binder。",
            )
        binder = BINDERS.get(binder_key)
        if binder is None:
            return self._failed(
                "WORKFLOW_BINDER_UNKNOWN",
                f"binder {binder_key} 未注册（静态注册表中不存在），节点 {node_id} 失败关闭。",
            )
        try:
            outcome = binder(
                turn_context=self.context.turn_context,
                slots=dict(self.context.slots),
                metadata=dict(metadata),
                user_message=self.source_user_message,
            )
        except BindError as exc:
            return self._failed(exc.code, exc.message)
        except (TypeError, ValueError) as exc:
            return self._failed(
                "WORKFLOW_BINDER_INVALID",
                f"binder {binder_key} 执行失败：{exc}",
            )
        if not isinstance(outcome, BindOutcome):
            return self._failed(
                "WORKFLOW_BINDER_INVALID",
                f"binder {binder_key} 返回了非 BindOutcome 结果。",
            )
        self.context.bind = {**self.context.bind, **outcome.bind}
        if outcome.missing_fields:
            # 绑定缺目标：不调 LLM、不调工具，立即 awaiting；停点字段经
            # structured_result.expected_fields 持久化（agent_loop 兜底）。
            return self._result(
                status="awaiting_user",
                reply_fragment=outcome.question
                or ("还需要补充：" + "、".join(outcome.missing_fields) + "。"),
                task_summary=f"工作流节点 {node_id}（bind:{binder_key}）缺少目标，等待用户。",
                slot_updates=dict(outcome.bind),
                structured_result={
                    "kind": "workflow_bind",
                    "expected_fields": list(outcome.missing_fields),
                    "bind": dict(outcome.bind),
                },
            )
        return self._result(
            status="completed",
            task_summary=f"工作流节点 {node_id}（bind:{binder_key}）完成。",
            slot_updates=dict(outcome.bind),
            next_step_id=outcome.next_step_id or self._next_step_id(metadata),
            structured_result={"kind": "workflow_bind", "bind": dict(outcome.bind)},
        )

    def _run_structured_llm_step(
        self,
        node_id: str,
        metadata: dict[str, Any],
    ) -> TaskExecutionResult:
        handler_key = str(metadata.get("handler") or "").strip()
        if not handler_key:
            return self._failed(
                "WORKFLOW_HANDLER_UNDECLARED",
                f"structured_llm 节点 {node_id} 未声明 metadata.handler。",
            )
        handler = STRUCTURED_STEPS.get(handler_key)
        if handler is None:
            return self._failed(
                "WORKFLOW_HANDLER_UNKNOWN",
                f"结构化步骤 {handler_key} 未注册（静态注册表中不存在），节点 {node_id} 失败关闭。",
            )
        max_llm_calls = self.pack.stop_policy.max_llm_calls
        if max_llm_calls is not None and self.context.llm_calls >= max_llm_calls:
            return self._failed(
                "WORKFLOW_BUDGET_EXCEEDED",
                f"已达到本帧 LLM 调用上限 {max_llm_calls}，节点 {node_id} 停止执行。",
                stop_reason="budget",
            )
        if self._llm_generate is None:
            return self._failed(
                "WORKFLOW_LLM_UNAVAILABLE",
                f"本帧没有可用的 LLM 通道，无法执行结构化步骤 {handler_key}。",
            )
        try:
            data = handler(
                user_message=self.source_user_message,
                turn_context=self.context.turn_context,
                bind=dict(self.context.bind),
                slots=dict(self.context.slots),
                outputs=dict(self.context.outputs),
                metadata=dict(metadata),
                llm=self._llm_generate,
            )
        except StructuredStepError as exc:
            self.context.llm_calls += 1
            if exc.ask_fields:
                # 越界计划：失败并追问，绝不重试写。
                return self._result(
                    status="awaiting_user",
                    reply_fragment=exc.message,
                    task_summary=f"工作流节点 {node_id}（{handler_key}）计划越界，已停下追问。",
                    structured_result={
                        "kind": handler_key,
                        "expected_fields": list(exc.ask_fields),
                    },
                )
            return self._failed(exc.code, str(exc))
        except LLMError as exc:
            self.context.llm_calls += 1
            return self._failed("WORKFLOW_LLM_FAILED", f"结构化步骤 {handler_key} 调用失败：{exc}")
        if not isinstance(data, dict):
            return self._failed(
                "WORKFLOW_STRUCTURE_INVALID",
                f"结构化步骤 {handler_key} 返回了非 object 结果。",
            )
        self.context.llm_calls += 1
        self.context.outputs[node_id] = {"status": "ok", "data": data}
        return self._result(
            status="completed",
            task_summary=f"工作流节点 {node_id}（structured_llm:{handler_key}）完成。",
            next_step_id=self._next_step_id(metadata),
            structured_result={"kind": handler_key, "data_keys": sorted(data)[:12]},
        )

    def _run_ask_step(
        self,
        node_id: str,
        node: dict[str, Any],
        metadata: dict[str, Any],
    ) -> TaskExecutionResult:
        template = str(
            metadata.get("question") or node.get("instruction") or ""
        ).strip()
        if not template:
            return self._failed(
                "WORKFLOW_NODE_INVALID",
                f"ask 节点 {node_id} 未声明提问内容。",
            )
        try:
            question = render_template(template, self._scopes())
        except UnresolvedExpression as exc:
            return self._failed(
                "EXPRESSION_UNRESOLVED",
                f"节点 {node_id} 提问模板解析失败：{exc}",
            )
        return self._result(
            status="awaiting_user",
            reply_fragment=question,
            task_summary=f"工作流节点 {node_id}（ask）等待用户补充。",
            structured_result={
                "kind": "workflow_ask",
                "expected_fields": _text_list(node.get("expected_user_info")),
            },
        )

    def _run_reply_step(
        self,
        node_id: str,
        node: dict[str, Any],
        metadata: dict[str, Any],
    ) -> TaskExecutionResult:
        template = str(
            metadata.get("template")
            or metadata.get("reply")
            or node.get("instruction")
            or ""
        ).strip()
        if not template:
            return self._failed(
                "WORKFLOW_NODE_INVALID",
                f"reply 节点 {node_id} 未声明回复内容。",
            )
        try:
            reply = render_template(template, self._scopes())
        except UnresolvedExpression as exc:
            return self._failed(
                "EXPRESSION_UNRESOLVED",
                f"节点 {node_id} 回复模板解析失败：{exc}",
            )
        return self._result(
            status="completed",
            reply_fragment=reply,
            task_summary=f"工作流节点 {node_id}（reply）完成。",
            next_step_id=self._next_step_id(metadata),
        )

    # -------------------------------------------------------------- helpers

    def _result(
        self,
        *,
        status: str,
        reply_fragment: str = "",
        task_summary: str = "",
        capability_results: list[dict[str, Any]] | None = None,
        next_step_id: str | None = None,
        slot_updates: dict[str, Any] | None = None,
        structured_result: Any | None = None,
    ) -> TaskExecutionResult:
        return TaskExecutionResult(
            task_frame_id=self.task_frame_id,
            status=status,  # type: ignore[arg-type]
            reply_fragment=reply_fragment,
            slot_updates=dict(slot_updates or {}),
            task_summary=task_summary,
            capability_results=capability_results or [],
            next_step_id=next_step_id,
            structured_result=structured_result,
            action_count=1,
            loop_checkpoint={
                **self._base_checkpoint,
                "workflow_state": self.context.to_checkpoint(),
            },
        )

    def _failed(
        self,
        code: str,
        message: str,
        *,
        stop_reason: str = "failed",
    ) -> TaskExecutionResult:
        return TaskExecutionResult(
            task_frame_id=self.task_frame_id,
            status="failed",
            reply_fragment=message,
            task_summary=f"工作流步骤失败（{code}）。",
            action_count=1,
            error={"code": code, "message": message, "stop_reason": stop_reason},
            loop_checkpoint={
                **self._base_checkpoint,
                "workflow_state": self.context.to_checkpoint(),
            },
        )


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]
