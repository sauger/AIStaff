from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.db.database import _migrate_model_ocr_role
from app.db.models import ModelConfig, Tenant
from app.harness import (
    HarnessExecutor,
    HarnessLimits,
    HarnessToolCall,
    HarnessToolContext,
    build_file_tool_registry,
)
from app.knowledge.parser import KnowledgeParseError, extract_text
from app.llm import LLMError
from app.security.encryption import encrypt_secret
from app.session.attachments import parse_chat_attachment


class RecordingOcrClient:
    calls: list[dict[str, object]]

    def __init__(self, config) -> None:
        self.config = config

    def generate_text(self, prompt, payload, *_args, **kwargs):
        images = kwargs.get("vision_image_data_urls") or []
        RecordingOcrClient.calls.append(
            {
                "model": self.config.model,
                "prompt": prompt,
                "payload": payload,
                "images": list(images),
            }
        )
        if "第 1 页" in str(payload):
            return "第一页合同正文"
        if "第 2 页" in str(payload):
            return "第二页签章"
        return "识别文本"


class FailingOcrClient:
    def __init__(self, _config) -> None:
        pass

    def generate_text(self, *_args, **_kwargs):
        raise LLMError("connection reset", provider_message="上游超时")


class EmptyOcrClient:
    def __init__(self, _config) -> None:
        pass

    def generate_text(self, *_args, **_kwargs):
        raise LLMError("Model returned an empty response")


def _blank_pdf(pages: int = 2) -> bytes:
    import pymupdf

    document = pymupdf.open()
    for _ in range(pages):
        document.new_page()
    payload = document.tobytes()
    document.close()
    return payload


def _text_pdf(text: str) -> bytes:
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


def _prepare_db(tmp_path: Path, monkeypatch, *, with_ocr: bool) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'ocr.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.db.database.engine", engine)
    db = Session(engine)
    db.add(Tenant(id="tenant_demo", name="Demo"))
    db.add(
        ModelConfig(
            id="model_chat",
            tenant_id="tenant_demo",
            name="DeepSeek",
            api_key_encrypted=encrypt_secret("chat-secret"),
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            trust_status="legacy_trusted",
            enabled=True,
            is_default=True,
            is_ocr=False,
        )
    )
    if with_ocr:
        db.add(
            ModelConfig(
                id="model_ocr",
                tenant_id="tenant_demo",
                name="GLM-4V",
                api_key_encrypted=encrypt_secret("ocr-secret"),
                model="glm-4v-plus",
                base_url="https://open.bigmodel.cn/api/paas/v4/",
                trust_status="legacy_trusted",
                enabled=True,
                is_default=False,
                is_ocr=True,
            )
        )
    db.commit()
    return db


def test_empty_pypdf_uses_ocr_model_for_every_page_and_keeps_chat_default(
    tmp_path: Path, monkeypatch
) -> None:
    RecordingOcrClient.calls = []
    monkeypatch.setattr("app.knowledge.pdf_ocr.LLMClient", RecordingOcrClient)
    db = _prepare_db(tmp_path, monkeypatch, with_ocr=True)
    try:
        text, file_type = extract_text("scan.pdf", _blank_pdf(2), tenant_id="tenant_demo", db=db)
    finally:
        db.close()

    assert file_type == "pdf"
    assert text.startswith("# PDF 文档\n\n## 第 1 页")
    assert "第一页合同正文" in text
    assert "## 第 2 页" in text
    assert "第二页签章" in text
    assert [call["model"] for call in RecordingOcrClient.calls] == ["glm-4v-plus", "glm-4v-plus"]
    assert all(
        str(image).startswith("data:image/jpeg;base64,")
        for call in RecordingOcrClient.calls
        for image in call["images"]
    )
    assert "deepseek-chat" not in {call["model"] for call in RecordingOcrClient.calls}


def test_missing_ocr_model_returns_chinese_setup_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.knowledge.pdf_ocr.LLMClient",
        lambda _config: (_ for _ in ()).throw(AssertionError("OCR client should not be created")),
    )
    db = _prepare_db(tmp_path, monkeypatch, with_ocr=False)
    try:
        with pytest.raises(KnowledgeParseError) as exc_info:
            extract_text("scan.pdf", _blank_pdf(1), tenant_id="tenant_demo", db=db)
        chat = db.get(ModelConfig, "model_chat")
    finally:
        db.close()

    message = str(exc_info.value)
    assert "扫描件" in message
    assert "OCR" in message
    assert "glm-4v-plus" in message
    assert chat is not None
    assert chat.is_default is True
    assert chat.is_ocr is False


def test_textual_pdf_does_not_call_ocr(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.knowledge.pdf_ocr.LLMClient",
        lambda _config: (_ for _ in ()).throw(AssertionError("textual PDF must stay on pypdf")),
    )
    db = _prepare_db(tmp_path, monkeypatch, with_ocr=True)
    try:
        text, file_type = extract_text(
            "policy.pdf",
            _text_pdf("Employee handbook leave policy"),
            tenant_id="tenant_demo",
            db=db,
        )
    finally:
        db.close()

    assert file_type == "pdf"
    assert "Employee handbook leave policy" in text
    assert text.startswith("# PDF 文档")


def test_effectively_empty_pdf_text_uses_ocr(tmp_path: Path, monkeypatch) -> None:
    class WhitespacePage:
        def extract_text(self) -> str:
            return " \n\x0c"

    class WhitespaceReader:
        def __init__(self) -> None:
            self.pages = [WhitespacePage()]

    monkeypatch.setattr("pypdf.PdfReader", lambda _stream: WhitespaceReader())
    RecordingOcrClient.calls = []
    monkeypatch.setattr("app.knowledge.pdf_ocr.LLMClient", RecordingOcrClient)
    db = _prepare_db(tmp_path, monkeypatch, with_ocr=True)
    try:
        text, _file_type = extract_text("scan.pdf", _blank_pdf(1), tenant_id="tenant_demo", db=db)
    finally:
        db.close()

    assert "第一页合同正文" in text
    assert len(RecordingOcrClient.calls) == 1


def test_ocr_failure_is_a_visible_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.knowledge.pdf_ocr.LLMClient", FailingOcrClient)
    db = _prepare_db(tmp_path, monkeypatch, with_ocr=True)
    try:
        with pytest.raises(KnowledgeParseError) as exc_info:
            extract_text("scan.pdf", _blank_pdf(1), tenant_id="tenant_demo", db=db)
    finally:
        db.close()

    message = str(exc_info.value)
    assert message != ""
    assert "第 1 页" in message
    assert "OCR 失败" in message
    assert "上游超时" in message


def test_empty_ocr_response_does_not_succeed_silently(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.knowledge.pdf_ocr.LLMClient", EmptyOcrClient)
    db = _prepare_db(tmp_path, monkeypatch, with_ocr=True)
    try:
        with pytest.raises(KnowledgeParseError) as exc_info:
            extract_text("scan.pdf", _blank_pdf(1), tenant_id="tenant_demo", db=db)
    finally:
        db.close()

    assert "没有识别到文字" in str(exc_info.value)


def test_chat_attachment_and_extract_tool_share_ocr_fallback(tmp_path: Path, monkeypatch) -> None:
    RecordingOcrClient.calls = []
    monkeypatch.setattr("app.knowledge.pdf_ocr.LLMClient", RecordingOcrClient)
    db = _prepare_db(tmp_path, monkeypatch, with_ocr=True)
    db.close()
    pdf = _blank_pdf(2)
    attachment = parse_chat_attachment(
        "scan.pdf",
        "application/pdf",
        pdf,
        tenant_id="tenant_demo",
    )

    assert attachment.error is None
    assert attachment.text is not None
    assert "第一页合同正文" in attachment.text
    assert "第二页签章" in attachment.text

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "scan.pdf").write_bytes(pdf)
    context = HarnessToolContext(
        run_id="run",
        task_frame_id="frame",
        tenant_id="tenant_demo",
        workspace_root=workspace.resolve(),
        limits=HarnessLimits(),
    )
    result = HarnessExecutor(build_file_tool_registry()).execute(
        context,
        HarnessToolCall(
            call_id="extract",
            name="extract_document_text",
            arguments={"path": "scan.pdf"},
        ),
    )
    assert result.success, result.error
    assert result.data is not None
    assert result.data["empty"] is False
    extracted = (workspace / "scan.pdf.extracted.txt").read_text(encoding="utf-8")
    assert "第一页合同正文" in extracted
    assert "第二页签章" in extracted


def test_extract_tool_reports_missing_ocr_model(tmp_path: Path, monkeypatch) -> None:
    db = _prepare_db(tmp_path, monkeypatch, with_ocr=False)
    db.close()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "scan.pdf").write_bytes(_blank_pdf(1))
    context = HarnessToolContext(
        run_id="run",
        task_frame_id="frame",
        tenant_id="tenant_demo",
        workspace_root=workspace.resolve(),
        limits=HarnessLimits(),
    )
    result = HarnessExecutor(build_file_tool_registry()).execute(
        context,
        HarnessToolCall(
            call_id="extract",
            name="extract_document_text",
            arguments={"path": "scan.pdf"},
        ),
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "DOCUMENT_EXTRACTION_FAILED"
    assert "扫描件" in result.error.message
    assert "OCR" in result.error.message


def test_ocr_timeout_budget_fails_clearly(tmp_path: Path, monkeypatch) -> None:
    RecordingOcrClient.calls = []
    monkeypatch.setattr("app.knowledge.pdf_ocr.LLMClient", RecordingOcrClient)
    monkeypatch.setattr("app.knowledge.pdf_ocr.OCR_TOTAL_BUDGET_SECONDS", -1)
    db = _prepare_db(tmp_path, monkeypatch, with_ocr=True)
    try:
        with pytest.raises(KnowledgeParseError) as exc_info:
            extract_text("scan.pdf", _blank_pdf(1), tenant_id="tenant_demo", db=db)
    finally:
        db.close()

    assert "超时" in str(exc_info.value)
    assert RecordingOcrClient.calls == []


def test_migrate_model_ocr_role_adds_column_and_unique_index(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE model_configs ("
                "id VARCHAR PRIMARY KEY, tenant_id VARCHAR, is_default BOOLEAN)"
            )
        )
        _migrate_model_ocr_role(conn, {"model_configs"})
        _migrate_model_ocr_role(conn, {"model_configs"})
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(model_configs)"))}
        index_sql = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'index' AND name = 'uq_model_configs_tenant_ocr'"
            )
        ).scalar_one()

    assert "is_ocr" in columns
    assert "WHERE is_ocr = 1" in index_sql
