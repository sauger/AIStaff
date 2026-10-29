"""Write Honcho deriver and embedding settings into the sidecar env file.

The file path comes from ``HONCHO_SIDECAR_ENV_FILE`` on the AIStaff process.
Values are the tenant's saved runtime settings. An empty API key is omitted so
an existing sidecar secret stays in place. Honcho reads the file only after
its api and deriver processes restart.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

SIDECAR_RESTART_MESSAGE = (
    "已写入 Honcho 环境文件。请重启 Honcho 的 api 与 deriver 后才会生效。"
    "已有向量数据后不要更换嵌入模型或维度，否则需要重建向量库。"
)
SIDECAR_PATH_UNSET_MESSAGE = (
    "未配置 HONCHO_SIDECAR_ENV_FILE，无法把模型设置写入 Honcho。"
    "请在 AIStaff 环境中设置该绝对路径（例如 /mnt/data/honcho/.env）后重试。"
)
SIDECAR_SAVED_ONLY_MESSAGE = (
    "模型设置已保存在本平台。未配置 HONCHO_SIDECAR_ENV_FILE，尚未写入 Honcho。"
    "配置该路径后点击「应用到 sidecar」。"
)
SIDECAR_MODELS_MISSING_MESSAGE = (
    "请先填写并保存对话抽取模型或向量嵌入模型，再应用到 sidecar。"
)

_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_RAW_VALUE = re.compile(r"^[A-Za-z0-9_./:@+-]+$")
_DIALECTIC_LEVELS = ("minimal", "low", "medium", "high", "max")
_TEXT_FEATURES = ("SUMMARY", "DREAM_DEDUCTION", "DREAM_INDUCTION")
_DEFAULT_EMBEDDING_DIMENSIONS = 1536


class HonchoSidecarApplyError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def build_honcho_model_env(
    *,
    deriver_model: str,
    deriver_base_url: str,
    deriver_api_key: str,
    embedding_model: str,
    embedding_base_url: str,
    embedding_api_key: str,
    embedding_dimensions: int | None,
) -> dict[str, str | None]:
    """Return env assignments. ``None`` deletes that key from the sidecar file."""

    updates: dict[str, str | None] = {}
    deriver = deriver_model.strip()
    embedding = embedding_model.strip()
    if not deriver and not embedding:
        raise HonchoSidecarApplyError(SIDECAR_MODELS_MISSING_MESSAGE)
    if deriver:
        base_url = deriver_base_url.strip() or None
        updates["DERIVER_MODEL_CONFIG__TRANSPORT"] = "openai"
        updates["DERIVER_MODEL_CONFIG__MODEL"] = deriver
        updates["DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL"] = base_url
        if deriver_api_key.strip():
            updates["LLM_OPENAI_API_KEY"] = deriver_api_key.strip()
        for level in _DIALECTIC_LEVELS:
            prefix = f"DIALECTIC_LEVELS__{level}__MODEL_CONFIG"
            updates[f"{prefix}__TRANSPORT"] = "openai"
            updates[f"{prefix}__MODEL"] = deriver
            updates[f"{prefix}__OVERRIDES__BASE_URL"] = base_url
        for name in _TEXT_FEATURES:
            updates[f"{name}_MODEL_CONFIG__TRANSPORT"] = "openai"
            updates[f"{name}_MODEL_CONFIG__MODEL"] = deriver
            updates[f"{name}_MODEL_CONFIG__OVERRIDES__BASE_URL"] = base_url
    if embedding:
        dimensions = (
            _DEFAULT_EMBEDDING_DIMENSIONS
            if embedding_dimensions is None
            else int(embedding_dimensions)
        )
        if dimensions < 1 or dimensions > 8192:
            raise HonchoSidecarApplyError("向量维度必须是 1–8192 之间的整数")
        updates["EMBEDDING_MODEL_CONFIG__TRANSPORT"] = "openai"
        updates["EMBEDDING_MODEL_CONFIG__MODEL"] = embedding
        updates["EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL"] = embedding_base_url.strip() or None
        if embedding_api_key.strip():
            updates["EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY"] = embedding_api_key.strip()
        updates["EMBEDDING_VECTOR_DIMENSIONS"] = str(dimensions)
    return updates


def apply_sidecar_env_file(path: str, updates: dict[str, str | None]) -> bool:
    """Write ``updates`` into the sidecar env file. Return whether the bytes changed."""

    target = resolve_sidecar_env_file(path)
    for value in updates.values():
        if isinstance(value, str):
            quote_env_value(value)
    try:
        original = target.read_text(encoding="utf-8") if target.is_file() else ""
        rendered = render_env(original, updates)
        if rendered == original and target.is_file():
            os.chmod(target, 0o600)
            return False
        _atomic_write(target, rendered)
    except HonchoSidecarApplyError:
        raise
    except OSError as exc:
        raise HonchoSidecarApplyError(
            f"无法写入 Honcho 环境文件：{exc.strerror or '未知错误'}"
        ) from exc
    return True


def resolve_sidecar_env_file(raw: str) -> Path:
    text = str(raw or "").strip()
    if not text:
        raise HonchoSidecarApplyError(SIDECAR_PATH_UNSET_MESSAGE)
    if "\x00" in text:
        raise HonchoSidecarApplyError("HONCHO_SIDECAR_ENV_FILE 路径无效")
    candidate = Path(text)
    if not candidate.is_absolute():
        raise HonchoSidecarApplyError("HONCHO_SIDECAR_ENV_FILE 必须是绝对路径")
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise HonchoSidecarApplyError("无法解析 HONCHO_SIDECAR_ENV_FILE") from exc
    if resolved.exists() and resolved.is_dir():
        raise HonchoSidecarApplyError(
            "HONCHO_SIDECAR_ENV_FILE 指向目录，需要 Honcho 的 .env 文件"
        )
    parent = resolved.parent
    if not parent.is_dir():
        raise HonchoSidecarApplyError("HONCHO_SIDECAR_ENV_FILE 所在目录不存在")
    if not os.access(parent, os.W_OK | os.X_OK):
        raise HonchoSidecarApplyError("HONCHO_SIDECAR_ENV_FILE 所在目录不可写")
    if resolved.exists() and not resolved.is_file():
        raise HonchoSidecarApplyError("HONCHO_SIDECAR_ENV_FILE 不是普通文件")
    return resolved


def quote_env_value(value: str) -> str:
    if any(char in value for char in "\r\n\x00"):
        raise HonchoSidecarApplyError("模型配置不能包含换行")
    if _RAW_VALUE.fullmatch(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def render_env(original: str, updates: dict[str, str | None]) -> str:
    text = original.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    written: set[str] = set()
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        match = None if not stripped or stripped.startswith("#") else _ASSIGNMENT.match(stripped)
        if match is None or match.group(1) not in updates:
            kept.append(line)
            continue
        key = match.group(1)
        value = updates[key]
        if key in written or value is None:
            continue
        written.add(key)
        kept.append(f"{key}={quote_env_value(value)}")
    missing = [key for key, value in updates.items() if key not in written and value is not None]
    if missing:
        if kept and kept[-1] != "":
            kept.append("")
        kept.append("# Managed by AIStaff runtime settings. Restart Honcho api and deriver.")
        kept.extend(f"{key}={quote_env_value(updates[key] or '')}" for key in missing)
    if not kept:
        return ""
    return "\n".join(kept) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=".honcho-env.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)
    logger.info("wrote honcho sidecar env file")
