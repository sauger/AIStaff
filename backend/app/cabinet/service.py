from __future__ import annotations

import base64
import mimetypes
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from app.cabinet.errors import CabinetError
from app.cabinet.paths import (
    atomic_write,
    cabinet_root,
    join_path,
    normalize_name,
    normalize_path,
    parent_path,
    sha256_bytes,
    storage_path,
)
from app.cabinet.schema import (
    CabinetEntryRead,
    CabinetFindMatch,
    CabinetFindResult,
    CabinetListResponse,
)
from app.cabinet.templates import apply_replacements, can_apply_replacements
from app.db.models import AgentProfile, EmployeeCabinetEntry, utc_now
from app.security.permissions import agent_owned_by_user, is_admin_user
from app.session.attachment_store import read_staged_chat_attachment
from app.session.session_schema import ChatAttachmentRead

CHAT_INBOX_FOLDER = "对话附件"
MAIL_ATTACHMENT_FOLDER = "邮件附件"
MAIL_INBOUND_FOLDER = "邮件附件/收件"
MAIL_OUTBOUND_FOLDER = "邮件附件/发件"
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_CABINET_BYTES = 200 * 1024 * 1024
MAX_PROMPT_ENTRIES = 80


def format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB".replace(".0 ", " ")
    return f"{value / (1024 * 1024):.1f} MB".replace(".0 ", " ")


def get_agent(db: Session, tenant_id: str, agent_id: str) -> AgentProfile:
    row = db.get(AgentProfile, agent_id)
    if row is None or row.tenant_id != tenant_id:
        raise CabinetError("CABINET_AGENT_NOT_FOUND", "员工不存在。", status_code=404)
    return row


def can_read_cabinet(agent: AgentProfile, user: Any) -> bool:
    return is_admin_user(user) or agent_owned_by_user(agent, user)


def can_write_cabinet(agent: AgentProfile, user: Any) -> bool:
    return agent_owned_by_user(agent, user)


def ensure_reader(db: Session, tenant_id: str, agent_id: str, user: Any) -> AgentProfile:
    agent = get_agent(db, tenant_id, agent_id)
    if can_read_cabinet(agent, user):
        return agent
    raise CabinetError(
        "CABINET_FORBIDDEN",
        "不能打开该员工的文件柜。",
        status_code=403,
    )


def ensure_writer(db: Session, tenant_id: str, agent_id: str, user: Any) -> AgentProfile:
    agent = get_agent(db, tenant_id, agent_id)
    if can_write_cabinet(agent, user):
        return agent
    raise CabinetError(
        "CABINET_WRITE_FORBIDDEN",
        "没有权限写入、删除、覆盖或重命名该员工文件柜中的文件。",
        status_code=403,
    )


def used_bytes(db: Session, tenant_id: str, agent_id: str) -> int:
    rows = db.exec(
        select(EmployeeCabinetEntry.size_bytes).where(
            EmployeeCabinetEntry.tenant_id == tenant_id,
            EmployeeCabinetEntry.agent_id == agent_id,
            EmployeeCabinetEntry.kind == "file",
        )
    ).all()
    return int(sum(int(value or 0) for value in rows))


def entry_read(row: EmployeeCabinetEntry) -> CabinetEntryRead:
    return CabinetEntryRead(
        id=row.id,
        kind="folder" if row.kind == "folder" else "file",
        name=row.name,
        path=row.path,
        parent_path=row.parent_path,
        size_bytes=row.size_bytes,
        content_type=row.content_type if row.kind == "file" else None,
        sha256=row.sha256,
        source=row.source,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def get_entry(
    db: Session, tenant_id: str, agent_id: str, path: str
) -> EmployeeCabinetEntry | None:
    normalized = normalize_path(path, allow_empty=True)
    if not normalized:
        return None
    return db.exec(
        select(EmployeeCabinetEntry).where(
            EmployeeCabinetEntry.tenant_id == tenant_id,
            EmployeeCabinetEntry.agent_id == agent_id,
            EmployeeCabinetEntry.path == normalized,
        )
    ).first()


def list_folder(
    db: Session,
    tenant_id: str,
    agent_id: str,
    path: str = "",
    *,
    can_write: bool,
) -> CabinetListResponse:
    folder = normalize_path(path, allow_empty=True)
    if folder:
        row = get_entry(db, tenant_id, agent_id, folder)
        if row is None or row.kind != "folder":
            raise CabinetError(
                "CABINET_FOLDER_NOT_FOUND",
                f"文件夹「{folder}」不存在。",
                status_code=404,
                details={"path": folder},
            )
    rows = db.exec(
        select(EmployeeCabinetEntry)
        .where(
            EmployeeCabinetEntry.tenant_id == tenant_id,
            EmployeeCabinetEntry.agent_id == agent_id,
            EmployeeCabinetEntry.parent_path == folder,
        )
        .order_by(col(EmployeeCabinetEntry.kind).desc(), EmployeeCabinetEntry.name)
    ).all()
    return CabinetListResponse(
        agent_id=agent_id,
        path=folder,
        can_write=can_write,
        used_bytes=used_bytes(db, tenant_id, agent_id),
        max_file_bytes=MAX_FILE_BYTES,
        max_cabinet_bytes=MAX_CABINET_BYTES,
        entries=[entry_read(item) for item in rows],
    )


def make_folder(
    db: Session,
    tenant_id: str,
    agent_id: str,
    path: str,
    name: str,
    *,
    source: str = "console",
) -> EmployeeCabinetEntry:
    folder = normalize_path(path, allow_empty=True)
    folder_name = normalize_name(name)
    _ensure_parent_folder(db, tenant_id, agent_id, folder, create=True, source=source)
    target = join_path(folder, folder_name)
    existing = get_entry(db, tenant_id, agent_id, target)
    if existing is not None:
        if existing.kind == "folder":
            return existing
        raise CabinetError(
            "CABINET_NAME_CONFLICT",
            f"「{target}」已存在同名文件，不能建成文件夹。",
            details={"path": target},
        )
    storage_path(tenant_id, agent_id, target).mkdir(parents=True, exist_ok=True)
    row = EmployeeCabinetEntry(
        tenant_id=tenant_id,
        agent_id=agent_id,
        kind="folder",
        name=folder_name,
        path=target,
        parent_path=folder,
        source=source,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def save_bytes(
    db: Session,
    tenant_id: str,
    agent_id: str,
    path: str,
    data: bytes,
    *,
    overwrite: bool = False,
    source: str = "console",
    content_type: str | None = None,
    create_parents: bool = True,
) -> EmployeeCabinetEntry:
    target = normalize_path(path, allow_empty=False)
    name = target.rsplit("/", 1)[-1]
    folder = parent_path(target)
    _ensure_parent_folder(
        db, tenant_id, agent_id, folder, create=create_parents, source=source
    )
    existing = get_entry(db, tenant_id, agent_id, target)
    if existing is not None and existing.kind == "folder":
        raise CabinetError(
            "CABINET_NAME_CONFLICT",
            f"「{target}」已是文件夹，不能写成文件。",
            details={"path": target},
        )
    if existing is not None and not overwrite:
        raise CabinetError(
            "CABINET_EXISTS",
            f"「{target}」已存在。请改名后再保存，或确认覆盖。",
            details={"path": target},
        )
    current_used = used_bytes(db, tenant_id, agent_id)
    if existing is not None:
        current_used -= int(existing.size_bytes or 0)
    _reject_size(name, len(data), current_used)
    digest = sha256_bytes(data)
    detected_type = (
        content_type
        or mimetypes.guess_type(name)[0]
        or "application/octet-stream"
    )
    atomic_write(storage_path(tenant_id, agent_id, target), data)
    now = utc_now()
    if existing is None:
        row = EmployeeCabinetEntry(
            tenant_id=tenant_id,
            agent_id=agent_id,
            kind="file",
            name=name,
            path=target,
            parent_path=folder,
            size_bytes=len(data),
            content_type=detected_type,
            sha256=digest,
            source=source,
        )
        db.add(row)
    else:
        existing.size_bytes = len(data)
        existing.content_type = detected_type
        existing.sha256 = digest
        existing.source = source
        existing.updated_at = now
        db.add(existing)
        row = existing
    db.commit()
    db.refresh(row)
    return row


def read_file_bytes(db: Session, tenant_id: str, agent_id: str, path: str) -> tuple[EmployeeCabinetEntry, bytes]:
    target = normalize_path(path, allow_empty=False)
    row = get_entry(db, tenant_id, agent_id, target)
    if row is None or row.kind != "file":
        raise CabinetError(
            "CABINET_FILE_NOT_FOUND",
            f"文件「{target}」不存在。",
            status_code=404,
            details={"path": target},
        )
    payload_path = storage_path(tenant_id, agent_id, target)
    try:
        data = payload_path.read_bytes()
    except OSError as exc:
        raise CabinetError(
            "CABINET_READ_FAILED",
            f"无法读取「{target}」。",
            status_code=500,
            details={"path": target},
        ) from exc
    return row, data


def delete_entry(db: Session, tenant_id: str, agent_id: str, path: str) -> None:
    target = normalize_path(path, allow_empty=False)
    row = get_entry(db, tenant_id, agent_id, target)
    if row is None:
        raise CabinetError(
            "CABINET_NOT_FOUND",
            f"「{target}」不存在。",
            status_code=404,
            details={"path": target},
        )
    descendants = _entries_at_or_under(db, tenant_id, agent_id, target)
    disk = storage_path(tenant_id, agent_id, target)
    for item in descendants:
        db.delete(item)
    db.commit()
    if disk.exists() or disk.is_symlink():
        if disk.is_dir():
            shutil.rmtree(disk, ignore_errors=True)
        else:
            disk.unlink(missing_ok=True)


def move_entry(
    db: Session,
    tenant_id: str,
    agent_id: str,
    path: str,
    destination_path: str,
    *,
    overwrite: bool = False,
) -> EmployeeCabinetEntry:
    source = normalize_path(path, allow_empty=False)
    destination = normalize_path(destination_path, allow_empty=False)
    if source == destination:
        row = get_entry(db, tenant_id, agent_id, source)
        if row is None:
            raise CabinetError("CABINET_NOT_FOUND", f"「{source}」不存在。", status_code=404)
        return row
    if destination == source or destination.startswith(f"{source}/"):
        raise CabinetError("CABINET_PATH_INVALID", "不能把文件夹移动到自己内部。")
    row = get_entry(db, tenant_id, agent_id, source)
    if row is None:
        raise CabinetError("CABINET_NOT_FOUND", f"「{source}」不存在。", status_code=404)
    dest_parent = parent_path(destination)
    _ensure_parent_folder(db, tenant_id, agent_id, dest_parent, create=True, source=row.source)
    existing = get_entry(db, tenant_id, agent_id, destination)
    if existing is not None:
        if not overwrite:
            raise CabinetError(
                "CABINET_EXISTS",
                f"「{destination}」已存在。请改名或确认覆盖。",
                details={"path": destination},
            )
        if existing.kind != row.kind:
            raise CabinetError(
                "CABINET_NAME_CONFLICT",
                f"「{destination}」类型不同，不能覆盖。",
                details={"path": destination},
            )
        delete_entry(db, tenant_id, agent_id, destination)
        db.refresh(row)
    source_disk = storage_path(tenant_id, agent_id, source)
    dest_disk = storage_path(tenant_id, agent_id, destination)
    dest_disk.parent.mkdir(parents=True, exist_ok=True)
    if source_disk.exists():
        source_disk.replace(dest_disk)
    now = utc_now()
    descendants = _entries_at_or_under(db, tenant_id, agent_id, source)
    for item in descendants:
        suffix = item.path[len(source) :]
        item.path = f"{destination}{suffix}"
        item.parent_path = parent_path(item.path) if item.path != destination else dest_parent
        if item.path == destination:
            item.name = destination.rsplit("/", 1)[-1]
        item.updated_at = now
        db.add(item)
    db.commit()
    moved = get_entry(db, tenant_id, agent_id, destination)
    if moved is None:
        raise CabinetError("CABINET_MOVE_FAILED", f"移动「{source}」失败。", status_code=500)
    return moved


def find_by_name(
    db: Session,
    tenant_id: str,
    agent_id: str,
    query: str,
) -> CabinetFindResult:
    needle = str(query or "").strip()
    if not needle:
        raise CabinetError("CABINET_NAME_INVALID", "查找文件时必须给出文件名或路径。")
    looks_like_path = "/" in needle.replace("\\", "/")
    if looks_like_path:
        try:
            as_path = normalize_path(needle, allow_empty=False)
        except CabinetError:
            as_path = ""
        if as_path:
            exact = get_entry(db, tenant_id, agent_id, as_path)
            if exact is not None and exact.kind == "file":
                return CabinetFindResult(
                    query=needle,
                    matches=[_find_match(exact)],
                    needs_clarification=False,
                    message="",
                )
    name = needle.rsplit("/", 1)[-1].strip()
    rows = db.exec(
        select(EmployeeCabinetEntry).where(
            EmployeeCabinetEntry.tenant_id == tenant_id,
            EmployeeCabinetEntry.agent_id == agent_id,
            EmployeeCabinetEntry.kind == "file",
            EmployeeCabinetEntry.name == name,
        )
    ).all()
    if not rows:
        lowered = name.lower()
        rows = [
            item
            for item in db.exec(
                select(EmployeeCabinetEntry).where(
                    EmployeeCabinetEntry.tenant_id == tenant_id,
                    EmployeeCabinetEntry.agent_id == agent_id,
                    EmployeeCabinetEntry.kind == "file",
                )
            ).all()
            if item.name.lower() == lowered
        ]
    matches = [_find_match(item) for item in rows]
    if len(matches) > 1:
        listed = "、".join(item.path for item in matches)
        return CabinetFindResult(
            query=needle,
            matches=matches,
            needs_clarification=True,
            message=(
                f"找到 {len(matches)} 个同名文件（{listed}）。"
                "请改用完整路径指定要用哪一个，不要擅自选用。"
            ),
        )
    if not matches:
        return CabinetFindResult(
            query=needle,
            matches=[],
            needs_clarification=False,
            message=f"文件柜中找不到「{needle}」。这不是知识库文档，请确认文件名或先上传。",
        )
    return CabinetFindResult(query=needle, matches=matches, needs_clarification=False, message="")


def produce_from_template(
    db: Session,
    tenant_id: str,
    agent_id: str,
    *,
    template: str,
    destination_path: str | None = None,
    overwrite: bool = False,
    replacements: dict[str, str] | None = None,
    source: str = "produce",
) -> EmployeeCabinetEntry:
    located = find_by_name(db, tenant_id, agent_id, template)
    if located.needs_clarification:
        raise CabinetError(
            "CABINET_AMBIGUOUS",
            located.message,
            details={"matches": [item.model_dump() for item in located.matches]},
        )
    if not located.matches:
        raise CabinetError("CABINET_FILE_NOT_FOUND", located.message, status_code=404)
    template_path = located.matches[0].path
    template_row, data = read_file_bytes(db, tenant_id, agent_id, template_path)
    changes = {
        str(old): str(new)
        for old, new in (replacements or {}).items()
        if str(old)
    }
    if not can_apply_replacements(template_row.name):
        raise CabinetError(
            "CABINET_TEMPLATE_UNPROCESSABLE",
            (
                f"无法按模板「{template_row.name}」生产文件：运行时不能改写该格式"
                f"（{Path(template_row.name).suffix or '无扩展名'}）。"
                "原模板未被覆盖，也没有生成新文件。"
            ),
            details={"path": template_path, "filename": template_row.name},
        )
    produced = apply_replacements(template_row.name, data, changes)
    if destination_path:
        destination = normalize_path(destination_path, allow_empty=False)
    else:
        destination = _unique_destination(
            db, tenant_id, agent_id, template_path, overwrite
        )
    if destination == template_path and not overwrite:
        raise CabinetError(
            "CABINET_TEMPLATE_PRESERVED",
            (
                f"按模板生产默认另存为新文件，不会覆盖原模板「{template_path}」。"
                "若要覆盖，请明确要求覆盖。"
            ),
            details={"path": template_path},
        )
    return save_bytes(
        db,
        tenant_id,
        agent_id,
        destination,
        produced,
        overwrite=overwrite,
        source=source,
        content_type=template_row.content_type,
        create_parents=True,
    )


def ingest_chat_attachments(
    db: Session,
    *,
    tenant_id: str,
    agent_id: str,
    user_id: str,
    attachments: Iterable[ChatAttachmentRead],
    folder: str = CHAT_INBOX_FOLDER,
) -> list[EmployeeCabinetEntry]:
    saved: list[EmployeeCabinetEntry] = []
    inbox = normalize_path(folder, allow_empty=False)
    make_folder(db, tenant_id, agent_id, "", inbox.split("/", 1)[0] if "/" in inbox else inbox, source="chat")
    for attachment in attachments:
        data = read_staged_chat_attachment(
            attachment,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if data is None:
            raise CabinetError(
                "CABINET_ATTACHMENT_MISSING",
                f"对话附件「{attachment.filename}」无法落入文件柜：未找到已暂存的文件内容。",
                details={"filename": attachment.filename},
            )
        filename = _unique_name(db, tenant_id, agent_id, inbox, attachment.filename)
        saved.append(
            save_bytes(
                db,
                tenant_id,
                agent_id,
                join_path(inbox, filename),
                data,
                overwrite=False,
                source="chat",
                content_type=attachment.content_type,
                create_parents=True,
            )
        )
    return saved


def prompt_context(db: Session, tenant_id: str, agent_id: str) -> str:
    rows = db.exec(
        select(EmployeeCabinetEntry)
        .where(
            EmployeeCabinetEntry.tenant_id == tenant_id,
            EmployeeCabinetEntry.agent_id == agent_id,
            EmployeeCabinetEntry.kind == "file",
        )
        .order_by(EmployeeCabinetEntry.path)
    ).all()
    lines = [
        "该员工有一份个人文件柜（长期工作文件，不是知识库，也不会在复制员工/技能/登录说明时带走）。",
        "按文件名或路径取用：cabinet_find / cabinet_list；读取时用 cabinet_read（文件会放到工作区，不要把整个二进制塞进回复）。",
        "以某文件为模板生产：cabinet_produce_from_template，默认另存为新文件、不覆盖原模板；仅当用户明确要求覆盖时才覆盖。",
        "处理不了的格式必须说明原因，不得假装已生成。任务产物不会自动入柜，必须 cabinet_save 显式保存。",
        "对话附件默认落在「对话附件/」。邮件附件单独落在「邮件附件/收件」和「邮件附件/发件」，不要和模板混放。",
    ]
    if not rows:
        lines.append("文件柜当前为空。用户可在控制台上传，或在对话中发送附件。")
        return "\n".join(lines)
    lines.append(f"当前文件（共 {len(rows)} 个，列出前 {min(len(rows), MAX_PROMPT_ENTRIES)} 个）：")
    for row in rows[:MAX_PROMPT_ENTRIES]:
        lines.append(f"- {row.path}（{format_bytes(row.size_bytes)}）")
    if len(rows) > MAX_PROMPT_ENTRIES:
        lines.append(f"- …其余 {len(rows) - MAX_PROMPT_ENTRIES} 个请用 cabinet_list 查看")
    return "\n".join(lines)


def copy_into_workspace(
    db: Session,
    tenant_id: str,
    agent_id: str,
    path: str,
    workspace_root: Path,
) -> tuple[EmployeeCabinetEntry, Path, str]:
    row, data = read_file_bytes(db, tenant_id, agent_id, path)
    relative = Path("cabinet") / row.path
    target = (workspace_root / relative).resolve()
    workspace_resolved = workspace_root.resolve()
    if not target.is_relative_to(workspace_resolved):
        raise CabinetError("CABINET_PATH_INVALID", "无法把文件放到当前工作区。")
    atomic_write(target, data)
    sandbox = f"/workspace/{relative.as_posix()}"
    return row, target, sandbox


def purge_agent_cabinet(db: Session, tenant_id: str, agent_id: str) -> None:
    rows = db.exec(
        select(EmployeeCabinetEntry).where(
            EmployeeCabinetEntry.tenant_id == tenant_id,
            EmployeeCabinetEntry.agent_id == agent_id,
        )
    ).all()
    for row in rows:
        db.delete(row)
    db.commit()
    root = cabinet_root(tenant_id, agent_id)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


def decode_base64_content(filename: str, content_base64: str) -> bytes:
    raw = str(content_base64 or "").strip()
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=False)
    except (ValueError, TypeError) as exc:
        raise CabinetError(
            "CABINET_DECODE_FAILED",
            f"无法解码上传文件「{filename}」。",
            details={"filename": filename},
        ) from exc


def _ensure_parent_folder(
    db: Session,
    tenant_id: str,
    agent_id: str,
    folder: str,
    *,
    create: bool,
    source: str,
) -> None:
    if not folder:
        return
    existing = get_entry(db, tenant_id, agent_id, folder)
    if existing is not None:
        if existing.kind != "folder":
            raise CabinetError(
                "CABINET_NAME_CONFLICT",
                f"「{folder}」已是文件，不能当作文件夹。",
                details={"path": folder},
            )
        return
    if not create:
        raise CabinetError(
            "CABINET_FOLDER_NOT_FOUND",
            f"文件夹「{folder}」不存在。",
            status_code=404,
            details={"path": folder},
        )
    parent = parent_path(folder)
    _ensure_parent_folder(db, tenant_id, agent_id, parent, create=True, source=source)
    name = folder.rsplit("/", 1)[-1]
    storage_path(tenant_id, agent_id, folder).mkdir(parents=True, exist_ok=True)
    db.add(
        EmployeeCabinetEntry(
            tenant_id=tenant_id,
            agent_id=agent_id,
            kind="folder",
            name=name,
            path=folder,
            parent_path=parent,
            source=source,
        )
    )
    db.commit()


def _entries_at_or_under(
    db: Session, tenant_id: str, agent_id: str, target: str
) -> list[EmployeeCabinetEntry]:
    prefix = f"{target}/"
    rows = db.exec(
        select(EmployeeCabinetEntry).where(
            EmployeeCabinetEntry.tenant_id == tenant_id,
            EmployeeCabinetEntry.agent_id == agent_id,
        )
    ).all()
    return [item for item in rows if item.path == target or item.path.startswith(prefix)]


def _reject_size(filename: str, size: int, current_used: int) -> None:
    if size > MAX_FILE_BYTES:
        raise CabinetError(
            "CABINET_FILE_TOO_LARGE",
            (
                f"文件「{filename}」大小 {format_bytes(size)}，超过单文件上限 "
                f"{format_bytes(MAX_FILE_BYTES)}。"
            ),
            details={
                "filename": filename,
                "size_bytes": size,
                "max_file_bytes": MAX_FILE_BYTES,
            },
        )
    if current_used + size > MAX_CABINET_BYTES:
        raise CabinetError(
            "CABINET_QUOTA_EXCEEDED",
            (
                f"文件「{filename}」写入后柜子将使用 "
                f"{format_bytes(current_used + size)}，超过该员工上限 "
                f"{format_bytes(MAX_CABINET_BYTES)}（当前已用 {format_bytes(current_used)}）。"
            ),
            details={
                "filename": filename,
                "size_bytes": size,
                "used_bytes": current_used,
                "max_cabinet_bytes": MAX_CABINET_BYTES,
            },
        )


def _find_match(row: EmployeeCabinetEntry) -> CabinetFindMatch:
    return CabinetFindMatch(
        path=row.path,
        name=row.name,
        kind="folder" if row.kind == "folder" else "file",
        size_bytes=row.size_bytes,
    )


def _unique_destination(
    db: Session,
    tenant_id: str,
    agent_id: str,
    template_path: str,
    overwrite: bool,
) -> str:
    if overwrite:
        return template_path
    folder = parent_path(template_path)
    name = template_path.rsplit("/", 1)[-1]
    stem = Path(name).stem or name
    suffix = Path(name).suffix
    candidate = join_path(folder, f"{stem}-新文件{suffix}")
    index = 2
    while get_entry(db, tenant_id, agent_id, candidate) is not None:
        candidate = join_path(folder, f"{stem}-新文件-{index}{suffix}")
        index += 1
    return candidate


def _unique_name(
    db: Session, tenant_id: str, agent_id: str, folder: str, filename: str
) -> str:
    base = normalize_name(filename)
    candidate = base
    stem = Path(base).stem or base
    suffix = Path(base).suffix
    index = 2
    while get_entry(db, tenant_id, agent_id, join_path(folder, candidate)) is not None:
        candidate = f"{stem}-{index}{suffix}"
        index += 1
    return candidate


def ensure_mail_attachment_zone(
    db: Session, tenant_id: str, agent_id: str, *, source: str = "mail"
) -> None:
    make_folder(db, tenant_id, agent_id, "", MAIL_ATTACHMENT_FOLDER, source=source)
    make_folder(db, tenant_id, agent_id, MAIL_ATTACHMENT_FOLDER, "收件", source=source)
    make_folder(db, tenant_id, agent_id, MAIL_ATTACHMENT_FOLDER, "发件", source=source)


def unique_mail_filename(
    db: Session,
    tenant_id: str,
    agent_id: str,
    folder: str,
    filename: str,
    *,
    token: str = "",
) -> str:
    base = normalize_name(filename)
    if get_entry(db, tenant_id, agent_id, join_path(folder, base)) is None:
        return base
    stem = Path(base).stem or base
    suffix = Path(base).suffix
    stamp = "".join(ch for ch in str(token) if ch.isalnum() or ch in "-_")[:18]
    stamp = stamp or utc_now().strftime("%Y%m%d%H%M%S")
    candidate = f"{stem}-{stamp}{suffix}"
    index = 2
    while get_entry(db, tenant_id, agent_id, join_path(folder, candidate)) is not None:
        candidate = f"{stem}-{stamp}-{index}{suffix}"
        index += 1
    return candidate
