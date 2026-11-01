"""OCR fallback for scanned PDFs.

Textual PDFs never reach this module. Callers keep using ``extract_text``;
when pypdf finds no meaningful characters, pages are rendered and sent to the
tenant's dedicated OCR/vision model through ``LLMClient`` image inputs.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import replace
from time import monotonic
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from app.db.models import ModelConfig
from app.knowledge.parser import KnowledgeParseError
from app.llm import LLMClient, LLMError
from app.llm.model_config_resolver import resolve_model_config_for_runtime

logger = logging.getLogger(__name__)

OCR_PAGE_TIMEOUT_SECONDS = 120.0
OCR_TOTAL_BUDGET_SECONDS = 20 * 60
OCR_MAX_SIDE_PX = 1600
OCR_MAX_IMAGE_BYTES = 3_500_000

SCANNED_PDF_NEEDS_OCR_MODEL = (
    "该 PDF 是扫描件，没有可提取的文本。"
    "请在「模型」页添加并启用一个 OCR/视觉模型，不要把它设为默认对话模型。"
    "智谱 GLM-4V 示例：协议选 OpenAI Chat Completions，"
    "Base URL 填 https://open.bigmodel.cn/api/paas/v4/ ，"
    "Model 填 glm-4v-plus，保存启用后设为「扫描件 OCR」。"
)
OCR_EMPTY_RESULT = (
    "扫描件 OCR 没有识别到文字。"
    "请确认「模型」页的 OCR/视觉模型支持图片输入（例如智谱 GLM-4V）。"
)
OCR_SYSTEM_PROMPT = (
    "你是文档 OCR 引擎。请逐字转写图片中的全部文字，保持原有段落和阅读顺序。"
    "不要解释、不要翻译、不要补充图片里没有的内容。"
    "如果这一页没有文字，只回复一个空字符串。"
)


def ocr_scanned_pdf(
    content: bytes,
    *,
    tenant_id: str | None,
    db: Any | None = None,
) -> list[str]:
    if not tenant_id:
        raise KnowledgeParseError(SCANNED_PDF_NEEDS_OCR_MODEL)
    config = _resolve_ocr_config(tenant_id, db if isinstance(db, Session) else None)
    try:
        import pymupdf
    except Exception as exc:  # pragma: no cover - packaging should include the wheel.
        raise KnowledgeParseError("缺少 PDF 页面渲染组件，无法对扫描件做 OCR。") from exc

    try:
        document = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise KnowledgeParseError(f"无法打开扫描件 PDF：{exc}") from exc

    started = monotonic()
    page_texts: list[str] = []
    client = LLMClient(config)
    try:
        page_total = document.page_count
        if page_total <= 0:
            raise KnowledgeParseError("PDF 没有可读取的页面。")
        logger.info(
            "scanned pdf ocr start tenant=%s pages=%s model=%s",
            tenant_id,
            page_total,
            config.model,
        )
        for index, page in enumerate(document, start=1):
            if monotonic() - started > OCR_TOTAL_BUDGET_SECONDS:
                raise KnowledgeParseError(
                    f"扫描件 OCR 超时，已停止在第 {index} 页（共 {page_total} 页）。"
                    "请拆分 PDF 后重试，或检查 OCR/视觉模型的响应速度。"
                )
            try:
                image = _render_page(page)
            except KnowledgeParseError:
                raise
            except Exception as exc:
                raise KnowledgeParseError(f"无法把扫描件第 {index} 页转成图片：{exc}") from exc
            page_texts.append(_recognize_page(client, index, image))
    finally:
        document.close()
    if not any(text.strip() for text in page_texts):
        raise KnowledgeParseError(OCR_EMPTY_RESULT)
    return page_texts


def _resolve_ocr_config(tenant_id: str, db: Session | None):
    owns_session = db is None
    if owns_session:
        from app.db.database import engine

        session = Session(engine)
    else:
        session = db
    try:
        row = session.exec(
            select(ModelConfig).where(
                ModelConfig.tenant_id == tenant_id,
                ModelConfig.is_ocr == True,
                ModelConfig.enabled == True,
            )
        ).first()
        if row is None:
            raise KnowledgeParseError(SCANNED_PDF_NEEDS_OCR_MODEL)
        try:
            resolved = resolve_model_config_for_runtime(session, tenant_id, row.id)
        except HTTPException as exc:
            raise KnowledgeParseError(_ocr_config_error(exc)) from exc
        return replace(resolved, timeout_seconds=OCR_PAGE_TIMEOUT_SECONDS)
    finally:
        if owns_session:
            session.close()


def _ocr_config_error(exc: HTTPException) -> str:
    detail = str(exc.detail)
    if detail == "MODEL_CONFIG_DISABLED":
        return "扫描件 OCR 模型已停用。请在「模型」页启用该 OCR/视觉模型。"
    if detail == "MODEL_CONFIG_VERIFICATION_REQUIRED":
        return "扫描件 OCR 模型尚未通过连接测试。请在「模型」页测试并启用该 OCR/视觉模型。"
    return "扫描件 OCR 模型不可用。请在「模型」页检查该 OCR/视觉模型配置。"


def _render_page(page: Any) -> bytes:
    import pymupdf

    rect = page.rect
    longest = max(float(rect.width), float(rect.height), 1.0)
    zoom = min(2.0, OCR_MAX_SIDE_PX / longest)
    zoom = max(0.4, zoom)
    quality = 80
    for _attempt in range(4):
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        try:
            image = pixmap.tobytes("jpeg", jpg_quality=quality)
        finally:
            pixmap = None
        if len(image) <= OCR_MAX_IMAGE_BYTES:
            return image
        zoom *= 0.7
        quality = max(50, quality - 10)
    raise KnowledgeParseError("扫描件页面图片过大，无法在内存限制内完成 OCR。")


def _recognize_page(client: LLMClient, index: int, image: bytes) -> str:
    encoded = base64.b64encode(image).decode("ascii")
    data_url = f"data:image/jpeg;base64,{encoded}"
    try:
        text = client.generate_text(
            OCR_SYSTEM_PROMPT,
            f"请转写第 {index} 页扫描件中的全部文字。",
            vision_image_data_urls=[data_url],
        )
    except LLMError as exc:
        if "empty response" in str(exc).lower():
            return ""
        detail = exc.provider_message or str(exc)
        raise KnowledgeParseError(f"扫描件第 {index} 页 OCR 失败：{detail}") from exc
    except KnowledgeParseError:
        raise
    except Exception as exc:
        raise KnowledgeParseError(f"扫描件第 {index} 页 OCR 失败：{exc}") from exc
    return text or ""
