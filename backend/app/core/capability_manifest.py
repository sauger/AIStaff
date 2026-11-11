from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlmodel import Session, select

from app.agents.branching import (
    get_agent,
    is_bound_resource_visible_for_agent,
    is_open_gallery_resource,
    visible_knowledge_base_versions,
    visible_tool_rows,
)
from app.capabilities.local_general_skill import package_from_row
from app.core.task_request_compiler import (
    CapabilityDescriptor,
    CapabilityManifest,
    current_step_capability_refs,
)
from app.db.models import (
    AgentResourceBinding,
    GeneralSkill,
    KnowledgeBase,
    MCPServer,
    Skill,
    Tool,
    UIConfig,
)
from app.harness import (
    build_file_tool_registry,
    register_command_tools,
    register_skill_script_tools,
)
from app.harness.sandbox import available_backend
from app.skills.tool_authorization import current_sop_tool_authorization

RESERVED_HARNESS_CAPABILITY_NAMES = {
    "capability_search",
    "capability_describe",
    "list_published_deliverables",
    "read_published_deliverable",
    "exec_command",
    "run_skill_script",
    "knowledge_search",
    "lark_cli",
    "wecom_oa_approval",
    "external_task_status",
    "cabinet_list",
    "cabinet_find",
    "cabinet_read",
    "cabinet_save",
    "cabinet_produce_from_template",
    "mail_list",
    "mail_read",
    "mail_draft",
    "mail_send",
    "secret_list",
    "login_guide_list",
    "secure_login",
    "render_dashboard",
    "compose_dashboard",
}


DEFAULT_CONVERSATION_ALLOWLIST = frozenset(
    {
        "get_screen",
        "describe_dataset",
        "list_component_types",
        "knowledge_search",
    }
)


def _workflow_tool_policy(agent_row: Any) -> dict[str, Any] | None:
    """Return the per-agent workflow policy, or None for legacy agents.

    Only employees that declare ``metadata.workflow`` get the narrowed
    conversation / SOP tool surfaces; every other employee keeps the existing
    manifest byte-for-byte.
    """

    metadata = getattr(agent_row, "metadata_json", None)
    workflow = metadata.get("workflow") if isinstance(metadata, dict) else None
    if not isinstance(workflow, dict):
        return None
    policy = workflow.get("tool_policy")
    return policy if isinstance(policy, dict) else {}


def _conversation_allowlist(policy: dict[str, Any]) -> frozenset[str]:
    configured = policy.get("conversation_allowlist")
    if isinstance(configured, list):
        names = {str(item).strip() for item in configured if str(item).strip()}
        if names:
            return frozenset(names)
    return DEFAULT_CONVERSATION_ALLOWLIST


def _is_workflow_skill(skill: Skill | None) -> bool:
    content = getattr(skill, "content_json", None)
    metadata = content.get("metadata") if isinstance(content, dict) else None
    workflow = metadata.get("workflow") if isinstance(metadata, dict) else None
    return (
        isinstance(workflow, dict)
        and str(workflow.get("runtime") or "") == "workflow_v1"
    )


class CapabilityAuthorizationError(RuntimeError):
    pass


class CapabilityManifestBuilder:
    def __init__(self, db: Session):
        self.db = db

    def build(
        self,
        tenant_id: str,
        agent_id: str | None,
        skill: Skill | None,
        step_id: str | None,
    ) -> CapabilityManifest:
        agent_row = get_agent(self.db, tenant_id, agent_id) if agent_id else None
        if agent_id and agent_row is None:
            raise CapabilityAuthorizationError("当前员工不存在、已归档或不属于该租户。")
        policy = _workflow_tool_policy(agent_row)
        is_workflow = _is_workflow_skill(skill)
        conversation_allowlist = (
            _conversation_allowlist(policy) if policy is not None else frozenset()
        )
        refs = current_step_capability_refs(skill, step_id)
        authorization = current_sop_tool_authorization(tenant_id, skill, step_id)
        available: list[CapabilityDescriptor] = []
        unavailable: list[CapabilityDescriptor] = []

        internal = _internal_capability_descriptors()
        if policy is not None and not is_workflow:
            internal = [item for item in internal if item.name in conversation_allowlist]
        available.extend(internal)
        lark_descriptor = _lark_cli_descriptor(self.db, tenant_id, agent_id)
        if lark_descriptor is not None and (
            policy is None or is_workflow or lark_descriptor.name in conversation_allowlist
        ):
            if lark_descriptor.available:
                available.append(lark_descriptor)
            else:
                unavailable.append(lark_descriptor)
        wecom_oa_descriptor = _wecom_oa_approval_descriptor(self.db, tenant_id, agent_id)
        if wecom_oa_descriptor is not None and (
            policy is None
            or is_workflow
            or wecom_oa_descriptor.name in conversation_allowlist
        ):
            if wecom_oa_descriptor.available:
                available.append(wecom_oa_descriptor)
            else:
                unavailable.append(wecom_oa_descriptor)
        ui_config = self.db.get(UIConfig, tenant_id)
        sandbox_enabled = bool(getattr(ui_config, "sandbox_enabled", False))

        builtin_registry = build_file_tool_registry()
        register_command_tools(builtin_registry)
        register_skill_script_tools(builtin_registry)
        for spec in builtin_registry.specs():
            if (
                policy is not None
                and not is_workflow
                and spec.name not in conversation_allowlist
            ):
                continue
            is_command = spec.name in {"exec_command", "run_skill_script"}
            available.append(
                CapabilityDescriptor(
                    capability_id=(
                        f"builtin.command.{spec.name}" if is_command else f"builtin.fs.{spec.name}"
                    ),
                    name=spec.name,
                    kind="file",
                    description=spec.description,
                    input_schema=dict(spec.input_schema),
                    read_only=spec.side_effect == "read",
                    metadata={
                        "provider": ("builtin.command" if is_command else "builtin.fs"),
                        "side_effect": spec.side_effect,
                        **(
                            {
                                "sandbox": (
                                    available_backend() or "unavailable"
                                    if sandbox_enabled
                                    else "disabled_by_admin"
                                )
                            }
                            if is_command
                            else {}
                        ),
                    },
                )
            )

        visible_general = _visible_general_skills(self.db, tenant_id, agent_id)
        general_by_ref = {ref: row for row in visible_general for ref in (row.id, row.slug)}
        for row in visible_general:
            scope = _scope(row)
            if scope is None:
                unavailable.append(
                    _unavailable(
                        row.id,
                        f"general_skill.{row.slug}",
                        "general_skill",
                        "general",
                        "能力范围配置无效，已按 fail-closed 禁用。",
                    )
                )
                continue
            explicitly_allowed = any(
                general_by_ref.get(ref) is row for ref in refs["general_skill_ids"]
            )
            if policy is not None and not explicitly_allowed:
                continue
            if scope == "sop_specific" and not explicitly_allowed:
                continue
            available.append(
                CapabilityDescriptor(
                    capability_id=row.id,
                    name=f"general_skill.{row.slug}",
                    kind="general_skill",
                    capability_scope=scope,
                    description=row.description or row.name,
                    read_only=True,
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "当前 TaskFrame 中要交给该技能处理的具体需求。",
                            },
                            "operation": {
                                "type": "string",
                                "enum": ["read"],
                                "description": (
                                    "使用 read 将经过快照校验的 SKILL.md 和包内文件说明加载到 "
                                    "当前 AgentLoop；技能只提供执行指导，不会生成或运行临时代码。"
                                ),
                            },
                        },
                        "required": ["query", "operation"],
                    },
                    metadata={
                        "slug": row.slug,
                        "display_name": row.name,
                        "content_digest": general_skill_snapshot_digest(row),
                        "package_digest": package_from_row(row).digest,
                        "execution_policy": "instructions_only",
                        "script_execution": "use_harness_tools",
                        "permissions": dict(row.permissions_json or {}),
                        "runtime_config": dict(row.runtime_config_json or {}),
                        "sop_explicitly_allowed": explicitly_allowed,
                    },
                )
            )

        visible_tools = visible_tool_rows(self.db, tenant_id, agent_id, include_inactive=False)
        tool_by_ref = {ref: row for row in visible_tools for ref in (row.id, row.name)}
        for row in visible_tools:
            app_config = (row.config_json or {}).get("mcp_apps")
            if isinstance(app_config, dict):
                visibility = app_config.get("visibility")
                if isinstance(visibility, list) and "model" not in visibility:
                    # App-only controls stay callable from their isolated view but are not
                    # advertised to the conversation model.
                    continue
            scope = _scope(row)
            if scope is None:
                unavailable.append(
                    _unavailable(
                        row.id,
                        row.name,
                        "tool",
                        "general",
                        "能力范围配置无效，已按 fail-closed 禁用。",
                    )
                )
                continue
            explicitly_allowed = authorization.explicitly_allows(row)
            if policy is not None:
                if is_workflow:
                    if not explicitly_allowed:
                        continue
                elif (
                    row.name not in conversation_allowlist
                    and row.id not in conversation_allowlist
                ):
                    continue
            if scope == "sop_specific" and not explicitly_allowed:
                continue
            if not authorization.permits_skill(row):
                if explicitly_allowed:
                    unavailable.append(
                        _unavailable(
                            row.id,
                            row.name,
                            "tool",
                            scope,
                            "当前工具的 allowed_skills 未授权该 SOP。",
                        )
                    )
                continue
            invocation_name = _available_invocation_name(row.name, row.id, available)
            harness_config = _tool_harness_config(row)
            read_only = _tool_read_only(row, harness_config)
            required_skill_slug = str(
                harness_config.get("required_general_skill_slug") or ""
            ).strip()
            available.append(
                CapabilityDescriptor(
                    capability_id=row.id,
                    name=invocation_name,
                    kind="tool",
                    capability_scope=scope,
                    description=row.description or row.display_name or row.name,
                    input_schema=dict(row.input_schema or {}),
                    read_only=read_only,
                    metadata={
                        "tool_type": row.tool_type,
                        "method": row.method,
                        "source_tool_name": row.name,
                        "display_name": row.display_name or row.name,
                        "content_digest": tool_snapshot_digest(self.db, row),
                        "sop_explicitly_allowed": explicitly_allowed,
                        **(
                            {"required_general_skill_slug": required_skill_slug}
                            if required_skill_slug
                            else {}
                        ),
                    },
                )
            )

        visible_knowledge = visible_knowledge_base_versions(
            self.db, tenant_id, agent_id, include_inactive=False
        )
        allowed_knowledge_ids: list[str] = []
        allowed_knowledge_version_ids: list[str] = []
        knowledge_version_by_base_id: dict[str, str] = {}
        knowledge_scope_by_base_id: dict[str, str] = {}
        knowledge_scopes: list[str] = []
        valid_knowledge_ids: set[str] = set()
        for kb_id, version in visible_knowledge.items():
            scope = _scope(version)
            if scope == "general":
                root = self.db.get(KnowledgeBase, kb_id)
                scope = _scope(root) if root is not None else scope
            if scope is None:
                if kb_id in refs["knowledge_base_ids"]:
                    unavailable.append(
                        _unavailable(
                            kb_id,
                            kb_id,
                            "knowledge",
                            "general",
                            "能力范围配置无效，已按 fail-closed 禁用。",
                        )
                    )
                continue
            if scope == "sop_specific" and kb_id not in refs["knowledge_base_ids"]:
                continue
            valid_knowledge_ids.add(kb_id)
            allowed_knowledge_ids.append(kb_id)
            allowed_knowledge_version_ids.append(version.id)
            knowledge_version_by_base_id[kb_id] = version.id
            knowledge_scope_by_base_id[kb_id] = scope
            knowledge_scopes.append(scope)
        if allowed_knowledge_ids:
            available.append(
                CapabilityDescriptor(
                    capability_id="knowledge.search",
                    name="knowledge_search",
                    kind="knowledge",
                    capability_scope=(
                        "sop_specific"
                        if all(scope == "sop_specific" for scope in knowledge_scopes)
                        else "general"
                    ),
                    description="检索当前 TaskFrame 已授权的企业知识库。",
                    read_only=True,
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "knowledge_base_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "max_chunks": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 12,
                            },
                        },
                        "required": ["query"],
                    },
                    metadata={
                        "allowed_knowledge_base_ids": allowed_knowledge_ids,
                        "allowed_knowledge_base_version_ids": (allowed_knowledge_version_ids),
                        "knowledge_version_by_base_id": knowledge_version_by_base_id,
                        "knowledge_scope_by_base_id": knowledge_scope_by_base_id,
                    },
                )
            )

        unavailable.extend(
            self._unavailable_explicit_refs(
                tenant_id,
                refs,
                general_by_ref,
                tool_by_ref,
                valid_knowledge_ids,
            )
        )
        snapshot_revision = _snapshot_revision(available, unavailable)
        return CapabilityManifest(
            available=available,
            unavailable_references=unavailable,
            snapshot_revision=snapshot_revision,
        )

    def _unavailable_explicit_refs(
        self,
        tenant_id: str,
        refs: dict[str, list[str]],
        general_by_ref: dict[str, GeneralSkill],
        tool_by_ref: dict[str, Tool],
        knowledge_ids: set[str],
    ) -> list[CapabilityDescriptor]:
        unavailable: list[CapabilityDescriptor] = []
        for ref in refs["general_skill_ids"]:
            if ref not in general_by_ref:
                unavailable.append(
                    _unavailable(
                        ref,
                        ref,
                        "general_skill",
                        "sop_specific",
                        _explicit_reason(self.db.get(GeneralSkill, ref), tenant_id),
                    )
                )
        for ref in refs["tool_ids"]:
            if ref not in tool_by_ref:
                unavailable.append(
                    _unavailable(
                        ref,
                        ref,
                        "tool",
                        "sop_specific",
                        _explicit_reason(self.db.get(Tool, ref), tenant_id),
                    )
                )
        for ref in refs["knowledge_base_ids"]:
            if ref not in knowledge_ids:
                unavailable.append(
                    _unavailable(
                        ref,
                        ref,
                        "knowledge",
                        "sop_specific",
                        _explicit_reason(self.db.get(KnowledgeBase, ref), tenant_id),
                    )
                )
        return unavailable


def _wecom_oa_approval_descriptor(
    db: Session, tenant_id: str, agent_id: str | None
) -> CapabilityDescriptor | None:
    """settings 未开启时不进入清单；开启即可调用（缺凭证由 service 返回可恢复错误）。"""

    from app.config import get_settings
    from app.wecom_oa.service import (
        CAPABILITY_ID,
        TOOL_NAME,
        WECOM_OA_DESCRIPTION,
        WECOM_OA_INPUT_SCHEMA,
    )

    del db, tenant_id, agent_id
    if not get_settings().wecom_oa_enabled:
        return None
    return CapabilityDescriptor(
        capability_id=CAPABILITY_ID,
        name=TOOL_NAME,
        kind="internal",
        description=WECOM_OA_DESCRIPTION,
        input_schema=dict(WECOM_OA_INPUT_SCHEMA),
        metadata={"provider": CAPABILITY_ID, "side_effect": "write"},
        available=True,
    )


def _lark_cli_descriptor(
    db: Session, tenant_id: str, agent_id: str | None
) -> CapabilityDescriptor | None:
    """settings 未开启时返回 None（清单完全不出现，保持既有行为）。

    开启即视为可用：应用凭据除 settings / 渠道绑定外，还可在对话内通过
    ``config init`` 现场建立（含 ``--new`` 创建新应用），无法在编译清单时
    预判缺失，缺凭据的具体指引由 service 层以可恢复错误给出。
    """

    from app.config import get_settings
    from app.lark_cli.service import LARK_CLI_DESCRIPTION, LARK_CLI_INPUT_SCHEMA

    del db, tenant_id, agent_id  # 凭据可对话内建立后不再需要预检数据库。
    settings = get_settings()
    if not settings.lark_cli_enabled:
        return None
    return CapabilityDescriptor(
        capability_id="builtin.lark_cli",
        name="lark_cli",
        kind="internal",
        description=LARK_CLI_DESCRIPTION,
        input_schema=dict(LARK_CLI_INPUT_SCHEMA),
        metadata={"provider": "builtin.lark_cli", "side_effect": "write"},
        available=True,
    )


def _internal_capability_descriptors() -> list[CapabilityDescriptor]:
    from app.cabinet.harness import cabinet_capability_descriptors
    from app.mail.harness import mail_capability_descriptors
    from app.secrets.harness import secret_capability_descriptors

    return [
        *cabinet_capability_descriptors(),
        *mail_capability_descriptors(),
        *secret_capability_descriptors(),
        CapabilityDescriptor(
            capability_id="builtin.knowledge.proposal",
            name="knowledge_ingest_proposal",
            kind="internal",
            description=(
                "Create a pending proposal to add one uploaded attachment to the current "
                "employee's private knowledge base. Use only when the user explicitly asks "
                "to add/update knowledge. This does not write knowledge data; the user must "
                "confirm the returned proposal before any ingest job is created."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "attachment_id": {
                        "type": "string",
                        "description": (
                            "Attachment id from the current turn, or omit to use the "
                            "single attachment from the most recent user message."
                        ),
                    },
                    "intent": {
                        "type": "string",
                        "enum": ["create", "replace", "undecided"],
                        "default": "undecided",
                    },
                    "target_knowledge_base_id": {
                        "type": "string",
                        "description": "Optional target KB id when the employee has multiple KBs.",
                    },
                },
                "additionalProperties": False,
            },
            metadata={"provider": "harness", "side_effect": "write_proposal"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.external_task.status",
            name="external_task_status",
            kind="internal",
            description=(
                "Query a StaffDeck detached business task by task_id. Use this when the user asks "
                "for the status of a previously submitted #taskid. Only the current user's tasks "
                "are visible."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "minLength": 1},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
            metadata={"provider": "harness", "side_effect": "read"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.deliverables.list",
            name="list_published_deliverables",
            kind="internal",
            description=(
                "List recent files published by earlier TaskFrames in this same conversation. "
                "Use this before continuing work from a document delivered in a previous turn."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 20,
                    },
                },
                "additionalProperties": False,
            },
            metadata={"provider": "harness", "side_effect": "read"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.deliverables.read",
            name="read_published_deliverable",
            kind="internal",
            description=(
                "Read UTF-8 content from one file returned by list_published_deliverables. "
                "Pass its task_frame_id and path exactly; use the continuation token when truncated."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_frame_id": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "minLength": 1},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                    "max_bytes": {"type": "integer", "minimum": 1},
                    "continuation_token": {"type": "string", "minLength": 1},
                },
                "required": ["task_frame_id", "path"],
                "additionalProperties": False,
            },
            metadata={"provider": "harness", "side_effect": "read"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.discovery.search",
            name="capability_search",
            kind="internal",
            description=(
                "Search the complete frozen capability catalog for skills and tools "
                "relevant to the current TaskRequirement. Use this when the compact "
                "catalog is truncated or no visible candidate is clearly suitable."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "kinds": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["general_skill", "tool", "knowledge", "file"],
                        },
                        "uniqueItems": True,
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 8,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            metadata={"provider": "harness", "side_effect": "read"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.discovery.describe",
            name="capability_describe",
            kind="internal",
            description=(
                "Load the full input schema for one or more authorized capabilities "
                "from the compact catalog or capability_search results. Described "
                "capabilities become callable in this TaskFrame AgentLoop."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "capabilities": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                        "maxItems": 8,
                        "uniqueItems": True,
                        "description": "Capability IDs or invocation names to activate.",
                    }
                },
                "required": ["capabilities"],
                "additionalProperties": False,
            },
            metadata={"provider": "harness", "side_effect": "read"},
        ),
        _dashboard_render_descriptor(),
        _dashboard_compose_descriptor(),
    ]


def _dashboard_render_descriptor() -> CapabilityDescriptor:
    """Generic board renderer: pure view, no data access of its own.

    Kept inline (no DB lookup) because rendering is a side-effect-free view:
    any producer that can build a RenderSpec may use it.
    """

    from app.dashboard.spec import render_input_json_schema

    return CapabilityDescriptor(
        capability_id="builtin.dashboard.render",
        name="render_dashboard",
        kind="internal",
        description=(
            "Render a data dashboard (title, KPI cards, charts and tables) inside the "
            "conversation. Use it when the user asks for a dashboard/大屏/看板, or when "
            "several related metrics, trends and breakdowns should be shown together. "
            "Pass real numbers inline; never invent values. Chart types: line, area, column, "
            "bar, stacked_column, pie, donut, scatter, gauge, funnel, table. Choose `intent` "
            "(trend/compare/composition/detail/kpi) and let the host pick the type; set "
            "`theme: \"tech\"` for a dark operations-wall look and `span: 2` for the hero "
            "chart so the board does not look flat. Only set `type` when the user explicitly "
            "asks for one. Prefer compose_dashboard when the board should be built from a raw "
            "data-tool result."
        ),
        input_schema=render_input_json_schema(),
        metadata={"provider": "dashboard", "side_effect": "view"},
        read_only=True,
    )


def _dashboard_compose_descriptor() -> CapabilityDescriptor:
    """Automatic board from a raw tool result: the model names blocks, the host lays out.

    Like ``render_dashboard`` this is a pure view: it only reads data the same
    TaskFrame already fetched (inline or as a ``sandbox_json_file`` reference).
    """

    from app.dashboard.spec import compose_input_json_schema

    return CapabilityDescriptor(
        capability_id="builtin.dashboard.compose",
        name="compose_dashboard",
        kind="internal",
        description=(
            "Turn one data-tool result into a dashboard in a single call: the host splits the "
            "JSON into blocks, picks KPI cards, charts or tables and lays them out on a 12x12 "
            "grid inside the conversation. Pass the tool result unchanged as `data`; when it is "
            "a sandbox_json_file reference, pass that reference object itself (resolved "
            "server-side) and never paste, summarise or retype rows. Never write charts, chart "
            "types or coordinates. Optional `plan` only names blocks: blocks[].path (e.g. "
            "blocks[0], result.monthly) or label, plus title/role/region; layout.template "
            "(overview/compare/pulse/detail/stack), layout.order, layout.hero. "
            "「左边排名右边趋势」 -> template=pulse; 「表格放最下面」 -> template=detail. Call "
            "with mode=outline first to list block paths when unsure. EMPTY_DATA: explain in "
            "text, do not retry. TOOL_RESULT_REFERENCE_*: re-run the data tool. After success "
            "summarise the findings in a few sentences without repeating the data."
        ),
        input_schema=compose_input_json_schema(),
        metadata={"provider": "dashboard", "side_effect": "view"},
        read_only=True,
    )


def _tool_harness_config(tool: Tool) -> dict[str, Any]:
    config = tool.config_json if isinstance(tool.config_json, dict) else {}
    harness = config.get("harness") if isinstance(config, dict) else None
    return dict(harness) if isinstance(harness, dict) else {}


def _tool_read_only(tool: Tool, harness_config: dict[str, Any] | None = None) -> bool:
    config = harness_config if harness_config is not None else _tool_harness_config(tool)
    explicit = config.get("read_only")
    if isinstance(explicit, bool):
        return explicit
    return str(tool.method or "").strip().upper() == "GET"


def tool_snapshot_digest(db: Session, tool: Tool) -> str:
    """Hash every persisted field that can change an external tool invocation."""

    server_payload: dict[str, Any] | None = None
    if tool.mcp_server_id:
        server = db.get(MCPServer, tool.mcp_server_id)
        if server is not None:
            server_payload = {
                "id": server.id,
                "tenant_id": server.tenant_id,
                "transport": server.transport,
                "url": server.url,
                "headers": server.headers_json or {},
                "command": server.command,
                "args": server.args_json or [],
                "env": server.env_json or {},
                "cwd": server.cwd,
                "enabled": server.enabled,
            }
    payload = {
        "id": tool.id,
        "tenant_id": tool.tenant_id,
        "name": tool.name,
        "tool_type": tool.tool_type,
        "method": tool.method,
        "url": tool.url,
        "headers": tool.headers_json or {},
        "auth": tool.auth_json or {},
        "config": tool.config_json or {},
        "input_schema": tool.input_schema or {},
        "output_schema": tool.output_schema or {},
        "allowed_skills": tool.allowed_skills_json or [],
        "mcp_server_id": tool.mcp_server_id,
        "capability_scope": tool.capability_scope,
        "enabled": tool.enabled,
        "mcp_server": server_payload,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def general_skill_snapshot_digest(skill: GeneralSkill) -> str:
    """Hash the package and every persisted field used by the skill runner."""

    package = package_from_row(skill)
    payload = {
        "id": skill.id,
        "tenant_id": skill.tenant_id,
        "slug": skill.slug,
        "name": skill.name,
        "description": skill.description,
        "homepage": skill.homepage,
        "package_digest": package.digest,
        "metadata": skill.metadata_json or {},
        "permissions": skill.permissions_json or {},
        "runtime_config": skill.runtime_config_json or {},
        "capability_scope": skill.capability_scope,
        "status": skill.status,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _visible_general_skills(
    db: Session, tenant_id: str, agent_id: str | None
) -> list[GeneralSkill]:
    agent = get_agent(db, tenant_id, agent_id)
    rows = db.exec(
        select(GeneralSkill).where(
            GeneralSkill.tenant_id == tenant_id,
            GeneralSkill.status == "published",
        )
    ).all()
    if agent_id and not agent:
        return []
    if not agent or agent.is_overall:
        return [
            row for row in rows if is_open_gallery_resource(db, tenant_id, "general_skill", row)
        ]
    bindings = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent.id,
            AgentResourceBinding.resource_type == "general_skill",
            AgentResourceBinding.status == "active",
        )
    ).all()
    by_id = {row.id: row for row in rows}
    visible: list[GeneralSkill] = []
    for binding in bindings:
        row = by_id.get(binding.resource_id)
        if row is not None and is_bound_resource_visible_for_agent(
            db, tenant_id, "general_skill", row, binding
        ):
            visible.append(row)
    return visible


def _scope(row: object | None) -> str | None:
    value = str(getattr(row, "capability_scope", "") or "").strip()
    return value if value in {"general", "sop_specific"} else None


def _unavailable(
    capability_id: str,
    name: str,
    kind: str,
    scope: str,
    reason: str,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=capability_id,
        name=name,
        kind=kind,  # type: ignore[arg-type]
        capability_scope=scope,  # type: ignore[arg-type]
        available=False,
        unavailable_reason=reason,
    )


def _explicit_reason(row: object | None, tenant_id: str) -> str:
    if row is None or str(getattr(row, "tenant_id", "")) != tenant_id:
        return "SOP 引用的能力不存在。"
    return "SOP 引用的能力未发布、未启用或未绑定到当前员工。"


def _snapshot_revision(
    available: list[CapabilityDescriptor],
    unavailable: list[CapabilityDescriptor],
) -> str:
    def stable_key(item: CapabilityDescriptor) -> tuple[str, str, str]:
        return (item.kind, item.name, item.capability_id)

    payload = {
        "available": [item.model_dump(mode="json") for item in sorted(available, key=stable_key)],
        "unavailable": [
            item.model_dump(mode="json") for item in sorted(unavailable, key=stable_key)
        ],
    }
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
    )


def _available_invocation_name(
    preferred: str,
    capability_id: str,
    available: list[CapabilityDescriptor],
) -> str:
    # Knowledge is appended after external tools, so reserve its stable builtin
    # name up front as well as every descriptor already emitted.
    used = {item.name for item in available} | RESERVED_HARNESS_CAPABILITY_NAMES
    if preferred not in used:
        return preferred
    base = f"external_tool.{capability_id}"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}.{suffix}"
        suffix += 1
    return candidate
