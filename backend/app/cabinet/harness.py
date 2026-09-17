from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from app.cabinet.errors import CabinetError
from app.cabinet.schema import CabinetProduceRequest, CabinetSaveRequest
from app.cabinet.service import (
    copy_into_workspace,
    find_by_name,
    list_folder,
    produce_from_template,
    save_bytes,
)
from app.core.task_request_compiler import CapabilityDescriptor
from app.harness.execution_context import SANDBOX_WORKSPACE

CABINET_TOOL_NAMES = (
    "cabinet_list",
    "cabinet_find",
    "cabinet_read",
    "cabinet_save",
    "cabinet_produce_from_template",
)


def cabinet_capability_descriptors() -> list[CapabilityDescriptor]:
    return [
        CapabilityDescriptor(
            capability_id="builtin.cabinet.list",
            name="cabinet_list",
            kind="internal",
            description=(
                "List this employee's personal file cabinet folder. "
                "Use this to find templates and working files by path. "
                "This is not the knowledge base."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Folder path inside the cabinet. Empty string is the root.",
                        "default": "",
                    }
                },
                "additionalProperties": False,
            },
            metadata={"provider": "cabinet", "side_effect": "read"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.cabinet.find",
            name="cabinet_find",
            kind="internal",
            description=(
                "Find a cabinet file by filename or path. If several files share the "
                "same name, ask the user which path to use; do not pick silently."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            metadata={"provider": "cabinet", "side_effect": "read"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.cabinet.read",
            name="cabinet_read",
            kind="internal",
            description=(
                "Copy one cabinet file into the current workspace and return its "
                "workspace path and metadata. Do not dump the binary into the reply. "
                "Use extract_document_text or file tools on the workspace path if you "
                "need to inspect or edit contents."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            metadata={"provider": "cabinet", "side_effect": "read"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.cabinet.save",
            name="cabinet_save",
            kind="internal",
            description=(
                "Explicitly save a workspace file into this employee's cabinet. "
                "Artifacts are not stored in the cabinet unless this tool (or "
                "cabinet_produce_from_template) is used. Default does not overwrite."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "workspace_path": {"type": "string", "minLength": 1},
                    "cabinet_path": {"type": "string", "minLength": 1},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["workspace_path", "cabinet_path"],
                "additionalProperties": False,
            },
            metadata={"provider": "cabinet", "side_effect": "write"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.cabinet.produce",
            name="cabinet_produce_from_template",
            kind="internal",
            description=(
                "Produce a new cabinet file from a named template. The original "
                "template is not overwritten unless overwrite is true. If the format "
                "cannot be processed, fail with a clear reason and do not create a "
                "fake file."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "template": {"type": "string", "minLength": 1},
                    "destination_path": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                    "replacements": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["template"],
                "additionalProperties": False,
            },
            metadata={"provider": "cabinet", "side_effect": "write"},
        ),
    ]


def invoke_cabinet_tool(
    invoker: Any,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    agent_id = str(getattr(invoker, "agent_id", "") or "").strip()
    if not agent_id:
        return _failure("CABINET_AGENT_REQUIRED", "当前运行没有绑定数字员工，不能使用文件柜。")
    try:
        if name == "cabinet_list":
            return _list(invoker, agent_id, arguments)
        if name == "cabinet_find":
            return _find(invoker, agent_id, arguments)
        if name == "cabinet_read":
            return _read(invoker, agent_id, arguments)
        if name == "cabinet_save":
            return _save(invoker, agent_id, arguments)
        if name == "cabinet_produce_from_template":
            return _produce(invoker, agent_id, arguments)
    except CabinetError as exc:
        return _failure(exc.code, exc.message, **exc.details)
    return _failure("UNSUPPORTED_INTERNAL_CAPABILITY", "不支持的文件柜能力。")


def _list(invoker: Any, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    listing = list_folder(
        invoker.db,
        invoker.tenant_id,
        agent_id,
        str(arguments.get("path") or ""),
        can_write=True,
    )
    return {
        "success": True,
        "data": {
            "path": listing.path,
            "used_bytes": listing.used_bytes,
            "entries": [item.model_dump(mode="json") for item in listing.entries],
            "notice": "这是当前员工自己的文件柜。不要读取其他员工的文件。",
        },
    }


def _find(invoker: Any, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = find_by_name(
        invoker.db,
        invoker.tenant_id,
        agent_id,
        str(arguments.get("name") or ""),
    )
    if result.needs_clarification:
        return _failure(
            "CABINET_AMBIGUOUS",
            result.message,
            matches=[item.model_dump(mode="json") for item in result.matches],
        )
    if not result.matches:
        return _failure("CABINET_FILE_NOT_FOUND", result.message)
    return {"success": True, "data": result.model_dump(mode="json")}


def _read(invoker: Any, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    located = find_by_name(
        invoker.db,
        invoker.tenant_id,
        agent_id,
        str(arguments.get("path") or ""),
    )
    if located.needs_clarification:
        return _failure("CABINET_AMBIGUOUS", located.message, matches=[
            item.model_dump(mode="json") for item in located.matches
        ])
    if not located.matches:
        return _failure("CABINET_FILE_NOT_FOUND", located.message)
    row, _target, sandbox = copy_into_workspace(
        invoker.db,
        invoker.tenant_id,
        agent_id,
        located.matches[0].path,
        invoker.workspace_root,
    )
    return {
        "success": True,
        "data": {
            "path": row.path,
            "workspace_path": sandbox,
            "name": row.name,
            "size_bytes": row.size_bytes,
            "content_type": row.content_type,
            "sha256": row.sha256,
            "notice": (
                "文件已复制到工作区。请用工作区路径继续处理；不要把整个文件二进制贴进回复。"
            ),
        },
    }


def _save(invoker: Any, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    request = CabinetSaveRequest.model_validate(arguments)
    data = _read_workspace_file(invoker.workspace_root, request.workspace_path)
    row = save_bytes(
        invoker.db,
        invoker.tenant_id,
        agent_id,
        request.cabinet_path,
        data,
        overwrite=request.overwrite,
        source="save",
        create_parents=True,
    )
    return {
        "success": True,
        "data": {
            "path": row.path,
            "name": row.name,
            "size_bytes": row.size_bytes,
            "sha256": row.sha256,
            "overwritten": request.overwrite,
            "notice": "已显式保存到文件柜。未调用此工具的任务产物不会入柜。",
        },
    }


def _produce(invoker: Any, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    request = CabinetProduceRequest.model_validate(arguments)
    row = produce_from_template(
        invoker.db,
        invoker.tenant_id,
        agent_id,
        template=request.template,
        destination_path=request.destination_path,
        overwrite=request.overwrite,
        replacements=request.replacements,
    )
    _row, _target, sandbox = copy_into_workspace(
        invoker.db,
        invoker.tenant_id,
        agent_id,
        row.path,
        invoker.workspace_root,
    )
    return {
        "success": True,
        "data": {
            "path": row.path,
            "workspace_path": sandbox,
            "name": row.name,
            "size_bytes": row.size_bytes,
            "sha256": row.sha256,
            "template_overwritten": request.overwrite,
            "notice": (
                "已按模板另存到文件柜。"
                if not request.overwrite
                else "已按要求覆盖指定文件。"
            ),
        },
    }


def _read_workspace_file(workspace_root: Path, workspace_path: str) -> bytes:
    relative = str(workspace_path or "").strip()
    if relative.startswith(f"{SANDBOX_WORKSPACE}/"):
        relative = relative[len(SANDBOX_WORKSPACE) + 1 :]
    relative = relative.lstrip("/")
    root = workspace_root.resolve()
    target = (root / PurePosixPath(relative)).resolve()
    if not target.is_relative_to(root):
        raise CabinetError("CABINET_PATH_INVALID", "工作区路径超出当前任务工作区。")
    if not target.is_file() or target.is_symlink():
        raise CabinetError(
            "CABINET_WORKSPACE_FILE_NOT_FOUND",
            f"工作区文件「{workspace_path}」不存在，无法保存到文件柜。",
        )
    try:
        return target.read_bytes()
    except OSError as exc:
        raise CabinetError(
            "CABINET_READ_FAILED",
            f"无法读取工作区文件「{workspace_path}」。",
        ) from exc


def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "retryable": False}
    error.update(details)
    return {"success": False, "error": error}
