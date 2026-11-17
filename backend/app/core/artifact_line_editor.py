"""Line-scoped editing for published Harness text artifacts.

The chat document sidebar renders an artifact line by line and submits one
line plus a natural-language instruction. The rewrite here stays structural:
only the requested source line is ever spliced, so unrelated paragraphs are
byte-identical after the edit. Word documents join the same interaction with
paragraph granularity: sidebar line N maps to docx paragraph N.
"""

from __future__ import annotations

import copy
import hashlib
import io
import logging
import os
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from sqlmodel import Session, select

from app.db.models import Message
from app.harness import (
    HarnessArtifactAccessError,
    normalize_harness_artifact_path,
    open_harness_artifact,
)
from app.llm import LLMClient
from app.observability.spans import llm_operation

logger = logging.getLogger(__name__)

EDITABLE_ARTIFACT_SUFFIXES = (".md", ".markdown", ".txt", ".docx")
DOCX_ARTIFACT_SUFFIX = ".docx"
MAX_EDITABLE_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_EDITABLE_ARTIFACT_LINES = 5000
MAX_REPLACEMENT_CHARS = 4000

LINE_EDIT_SYSTEM_PROMPT = """\
你是对话式文档编辑助手。用户正在逐行审阅一份文档，并针对其中一行提出修改要求。

你的任务：只改写给定的这一行，输出改写后的内容。

规则：
- 只输出改写后的这一行文本，不要输出任何解释、开场白、引号包裹或 Markdown 代码围栏。
- 其余行一律不动；如果确实需要拆成多行，用换行符分隔，但仍只覆盖这一行的范围。
- 目标文档可能是 Markdown 文本行，也可能是 Word 段落；保持这一行原有的格式约定（Markdown 标记、缩进、列表符号、编号），除非修改要求明确要求改变。
- 修改要求与该行内容冲突或无法执行时，原样输出这一行。\
"""


class ArtifactLineEditError(RuntimeError):
    """Raised when a line edit cannot be prepared or applied."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def is_editable_artifact_path(raw_path: str) -> bool:
    lowered = raw_path.replace("\\", "/").lower()
    return lowered.endswith(EDITABLE_ARTIFACT_SUFFIXES)


def is_docx_artifact_path(raw_path: str) -> bool:
    return raw_path.replace("\\", "/").lower().endswith(DOCX_ARTIFACT_SUFFIX)


def read_editable_artifact_text(workspace_root: Path, raw_path: str) -> tuple[str, str, int]:
    """Read one published artifact as UTF-8 text plus its digest and size."""

    opened = open_harness_artifact(workspace_root, raw_path)
    try:
        if opened.size > MAX_EDITABLE_ARTIFACT_BYTES:
            raise ArtifactLineEditError(
                "ARTIFACT_TOO_LARGE",
                "文档超过行内编辑的大小限制，请下载后查看。",
            )
        data = b"".join(opened.iter_bytes())
    finally:
        opened.close()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactLineEditError(
            "ARTIFACT_NOT_TEXT",
            "该文件不是 UTF-8 文本，暂不支持行内编辑。",
        ) from exc
    if "\x00" in text:
        raise ArtifactLineEditError(
            "ARTIFACT_NOT_TEXT",
            "该文件不是 UTF-8 文本，暂不支持行内编辑。",
        )
    if len(text.split("\n")) > MAX_EDITABLE_ARTIFACT_LINES:
        raise ArtifactLineEditError(
            "ARTIFACT_TOO_LARGE",
            "文档行数超过行内编辑的限制，请下载后查看。",
        )
    return text, hashlib.sha256(data).hexdigest(), len(data)


def source_lines(text: str) -> list[str]:
    """Split with the same \"\\n\" semantics the sidebar uses to number lines."""

    return text.split("\n")


def strip_line_carriage(line: str) -> str:
    return line.removesuffix("\r")


def replace_source_line(text: str, line_number: int, replacement: str) -> str:
    """Splice one 1-based line, preserving the file's carriage-return style."""

    lines = source_lines(text)
    index = line_number - 1
    original = lines[index]
    carriage = "\r" if original.endswith("\r") else ""
    lines[index] = strip_line_carriage(replacement) + carriage
    return "\n".join(lines)


def write_harness_artifact_text(workspace_root: Path, raw_path: str, text: str) -> tuple[str, int]:
    return write_harness_artifact_bytes(workspace_root, raw_path, text.encode("utf-8"))


def write_harness_artifact_bytes(
    workspace_root: Path, raw_path: str, data: bytes
) -> tuple[str, int]:
    """Atomically overwrite one workspace file and return its new digest/size."""

    normalized = normalize_harness_artifact_path(raw_path)
    target = Path(workspace_root).joinpath(*PurePosixPath(normalized).parts)
    temporary = target.parent / f".harness-edit-{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return hashlib.sha256(data).hexdigest(), len(data)


def _load_docx(data: bytes):
    from docx import Document

    return Document(io.BytesIO(data))


def _set_docx_paragraph_text(paragraph, text: str) -> None:
    """Rewrite one paragraph in place, keeping the first run's font styling."""

    runs = paragraph.runs
    if runs:
        for run in runs[1:]:
            run._element.getparent().remove(run._element)
        runs[0].text = text
    else:
        paragraph.add_run(text)


def read_docx_artifact_paragraphs(
    workspace_root: Path, raw_path: str
) -> tuple[bytes, list[str], str, int]:
    """Read one published Word artifact as paragraph texts plus file identity."""

    opened = open_harness_artifact(workspace_root, raw_path)
    try:
        if opened.size > MAX_EDITABLE_ARTIFACT_BYTES:
            raise ArtifactLineEditError(
                "ARTIFACT_TOO_LARGE",
                "文档超过行内编辑的大小限制，请下载后查看。",
            )
        data = b"".join(opened.iter_bytes())
    finally:
        opened.close()
    try:
        paragraphs = [paragraph.text for paragraph in _load_docx(data).paragraphs]
    except Exception as exc:
        raise ArtifactLineEditError(
            "ARTIFACT_NOT_DOCX",
            "该文件不是可解析的 Word 文档，无法行内编辑。",
        ) from exc
    if len(paragraphs) > MAX_EDITABLE_ARTIFACT_LINES:
        raise ArtifactLineEditError(
            "ARTIFACT_TOO_LARGE",
            "文档行数超过行内编辑的限制，请下载后查看。",
        )
    return data, paragraphs, hashlib.sha256(data).hexdigest(), len(data)


def write_docx_paragraph_edit(
    workspace_root: Path,
    raw_path: str,
    data: bytes,
    paragraph_index: int,
    replacement_lines: list[str],
) -> tuple[str, int]:
    """Replace exactly one docx paragraph (extra lines clone its paragraph style)."""

    from docx.text.paragraph import Paragraph as DocxParagraph

    document = _load_docx(data)
    paragraphs = document.paragraphs
    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        raise ArtifactLineEditError(
            "LINE_OUT_OF_RANGE",
            "该段落不存在，请刷新文档后重试。",
        )
    target = paragraphs[paragraph_index]
    _set_docx_paragraph_text(target, replacement_lines[0])
    anchor = target._p
    for extra in replacement_lines[1:]:
        cloned = copy.deepcopy(target._p)
        anchor.addnext(cloned)
        anchor = cloned
        _set_docx_paragraph_text(DocxParagraph(cloned, target._parent), extra)
    buffer = io.BytesIO()
    document.save(buffer)
    return write_harness_artifact_bytes(workspace_root, raw_path, buffer.getvalue())


def normalize_model_replacement(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        fence_lines = cleaned.split("\n")
        body = fence_lines[1:]
        if len(fence_lines) >= 2 and fence_lines[-1].strip().startswith("```"):
            body = fence_lines[1:-1]
        cleaned = "\n".join(body).strip()
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    if not cleaned:
        raise ArtifactLineEditError(
            "LINE_EDIT_EMPTY_REWRITE",
            "改写结果为空，请调整修改要求后重试。",
        )
    if len(cleaned) > MAX_REPLACEMENT_CHARS:
        raise ArtifactLineEditError(
            "LINE_EDIT_REWRITE_TOO_LONG",
            "改写结果过长，请把修改要求拆得更具体后重试。",
        )
    return cleaned


def rewrite_artifact_line(
    model_config: Any,
    *,
    display_name: str,
    line_number: int,
    original_line: str,
    instruction: str,
) -> str:
    """Ask the runtime model to rewrite exactly one line from its own text."""

    prompt = (
        f"文件：{display_name}\n"
        f"第 {line_number} 行原文：\n{original_line}\n\n"
        f"修改要求：\n{instruction.strip()}\n\n"
        "请输出改写后的这一行。"
    )
    with llm_operation("artifact.line_edit"):
        raw = LLMClient(model_config).generate_text(LINE_EDIT_SYSTEM_PROMPT, prompt)
    return normalize_model_replacement(raw)


def sync_published_artifact_digests(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
    task_frame_id: str,
    path: str,
    sha256: str,
    size: int,
) -> int:
    """Keep every published artifact entry consistent after an in-place edit."""

    normalized = normalize_harness_artifact_path(path)
    rows = db.exec(
        select(Message).where(
            Message.tenant_id == tenant_id,
            Message.session_id == session_id,
            Message.role == "assistant",
        )
    ).all()
    updated = 0
    for row in rows:
        metadata = row.metadata_json or {}
        artifacts = metadata.get("harness_artifacts")
        if not isinstance(artifacts, list):
            continue
        changed = False
        next_artifacts: list[object] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict) or artifact.get("type") != "workspace_file":
                next_artifacts.append(artifact)
                continue
            if str(artifact.get("task_frame_id") or "") != task_frame_id:
                next_artifacts.append(artifact)
                continue
            stored_path = artifact.get("path")
            if not isinstance(stored_path, str):
                next_artifacts.append(artifact)
                continue
            try:
                stored_normalized = normalize_harness_artifact_path(stored_path)
            except HarnessArtifactAccessError:
                next_artifacts.append(artifact)
                continue
            if stored_normalized != normalized:
                next_artifacts.append(artifact)
                continue
            if artifact.get("sha256") == sha256 and artifact.get("size") == size:
                next_artifacts.append(artifact)
                continue
            next_artifact = dict(artifact)
            next_artifact["sha256"] = sha256
            next_artifact["size"] = size
            next_artifacts.append(next_artifact)
            changed = True
        if changed:
            row.metadata_json = {**metadata, "harness_artifacts": next_artifacts}
            db.add(row)
            updated += 1
    return updated


__all__ = [
    "DOCX_ARTIFACT_SUFFIX",
    "EDITABLE_ARTIFACT_SUFFIXES",
    "LINE_EDIT_SYSTEM_PROMPT",
    "MAX_EDITABLE_ARTIFACT_BYTES",
    "MAX_EDITABLE_ARTIFACT_LINES",
    "ArtifactLineEditError",
    "is_docx_artifact_path",
    "is_editable_artifact_path",
    "normalize_model_replacement",
    "read_docx_artifact_paragraphs",
    "read_editable_artifact_text",
    "replace_source_line",
    "rewrite_artifact_line",
    "source_lines",
    "strip_line_carriage",
    "sync_published_artifact_digests",
    "write_docx_paragraph_edit",
    "write_harness_artifact_bytes",
    "write_harness_artifact_text",
]
