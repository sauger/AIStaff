from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from app import paths as app_paths
from app.cabinet.errors import CabinetError

MAX_NAME_LENGTH = 180
MAX_PATH_LENGTH = 500
_UNSAFE_NAME = re.compile(r"[\x00-\x1f\x7f]")


def cabinet_root(tenant_id: str, agent_id: str) -> Path:
    data_root = app_paths.user_data_dir().resolve()
    root = (data_root / "employee_cabinets" / tenant_id / agent_id).resolve()
    if not root.is_relative_to(data_root):
        raise CabinetError("CABINET_PATH_INVALID", "文件柜存储路径无效。", status_code=500)
    return root


def normalize_path(value: str | None, *, allow_empty: bool = True) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    if raw.startswith("/"):
        raw = raw.lstrip("/")
    if not raw:
        if allow_empty:
            return ""
        raise CabinetError("CABINET_PATH_INVALID", "文件路径不能为空。")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise CabinetError("CABINET_PATH_INVALID", "文件路径不能包含上级目录。")
    if not parts:
        if allow_empty:
            return ""
        raise CabinetError("CABINET_PATH_INVALID", "文件路径不能为空。")
    names = [normalize_name(part) for part in parts]
    joined = "/".join(names)
    if len(joined) > MAX_PATH_LENGTH:
        raise CabinetError(
            "CABINET_PATH_INVALID",
            f"路径长度 {len(joined)} 超过上限 {MAX_PATH_LENGTH} 个字符。",
            details={"path_length": len(joined), "max_path_length": MAX_PATH_LENGTH},
        )
    return joined


def normalize_name(value: str) -> str:
    name = str(value or "").replace("\\", "/").strip()
    name = name.rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."}:
        raise CabinetError("CABINET_NAME_INVALID", "文件或文件夹名称无效。")
    if _UNSAFE_NAME.search(name):
        raise CabinetError("CABINET_NAME_INVALID", "文件或文件夹名称包含非法字符。")
    if len(name) > MAX_NAME_LENGTH:
        raise CabinetError(
            "CABINET_NAME_INVALID",
            f"名称长度 {len(name)} 超过上限 {MAX_NAME_LENGTH} 个字符。",
            details={"name_length": len(name), "max_name_length": MAX_NAME_LENGTH},
        )
    return name


def parent_path(path: str) -> str:
    normalized = normalize_path(path, allow_empty=False)
    if "/" not in normalized:
        return ""
    return normalized.rsplit("/", 1)[0]


def join_path(folder: str, name: str) -> str:
    folder_path = normalize_path(folder, allow_empty=True)
    file_name = normalize_name(name)
    return f"{folder_path}/{file_name}" if folder_path else file_name


def storage_path(tenant_id: str, agent_id: str, relative: str) -> Path:
    root = cabinet_root(tenant_id, agent_id)
    target = (root / relative).resolve() if relative else root
    if not target.is_relative_to(root):
        raise CabinetError("CABINET_PATH_INVALID", "文件路径超出该员工文件柜。")
    return target


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise CabinetError("CABINET_STORAGE_INVALID", "文件柜存储不能使用符号链接。", status_code=500)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def storage_identity(tenant_id: str, agent_id: str) -> str:
    payload = json.dumps([tenant_id, agent_id], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
