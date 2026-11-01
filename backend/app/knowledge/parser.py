from __future__ import annotations

import re
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile


SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".html", ".htm", ".pdf", ".docx", ".doc"}


class KnowledgeParseError(ValueError):
    pass


def extract_text(
    filename: str,
    content: bytes,
    *,
    tenant_id: str | None = None,
    db: Any | None = None,
) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise KnowledgeParseError(f"暂不支持 {suffix or 'unknown'} 文件格式。")
    if suffix == ".doc":
        raise KnowledgeParseError("暂不支持旧版 .doc 二进制格式，请转换为 .docx 后上传。")
    if suffix in {".txt", ".md", ".markdown"}:
        return _decode_text(content), suffix.lstrip(".")
    if suffix in {".html", ".htm"}:
        return _extract_html(content), "html"
    if suffix == ".pdf":
        return _extract_pdf(content, tenant_id=tenant_id, db=db), "pdf"
    if suffix == ".docx":
        return _extract_docx(content), "docx"
    raise KnowledgeParseError(f"暂不支持 {suffix} 文件格式。")


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def _extract_html(content: bytes) -> str:
    text = _decode_text(content)
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(text, "html.parser")
        for item in soup(["script", "style", "noscript"]):
            item.decompose()
        return soup.get_text("\n")
    except Exception:
        parser = _HTMLTextExtractor()
        parser.feed(text)
        return parser.text


_MEANINGFUL_PDF_TEXT = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")


def _extract_pdf(
    content: bytes,
    *,
    tenant_id: str | None = None,
    db: Any | None = None,
) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - dependency availability differs by env.
        raise KnowledgeParseError("缺少 pypdf，无法解析 PDF。") from exc
    try:
        reader = PdfReader(BytesIO(content))
        page_texts = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise KnowledgeParseError(f"无法解析 PDF：{exc}") from exc
    if not page_texts:
        raise KnowledgeParseError("PDF 没有可读取的页面。")
    # Textual PDFs stay on this fast path. OCR runs only when pypdf found no
    # letters, digits, or CJK characters (a scanned or otherwise empty text layer).
    if _MEANINGFUL_PDF_TEXT.search("".join(page_texts)):
        return _format_pdf_pages(page_texts)
    from app.knowledge.pdf_ocr import ocr_scanned_pdf

    recognized = ocr_scanned_pdf(content, tenant_id=tenant_id, db=db)
    text = _format_pdf_pages(recognized)
    if not text.strip():
        from app.knowledge.pdf_ocr import OCR_EMPTY_RESULT

        raise KnowledgeParseError(OCR_EMPTY_RESULT)
    return text


def _format_pdf_pages(page_texts: list[str]) -> str:
    blocks: list[str] = []
    for index, page_text in enumerate(page_texts, start=1):
        stripped = page_text.strip()
        if stripped:
            blocks.append(f"## 第 {index} 页\n\n{stripped}")
    if not blocks:
        return ""
    return "# PDF 文档\n\n" + "\n\n".join(blocks)


def _extract_docx(content: bytes) -> str:
    try:
        from docx import Document

        document = Document(BytesIO(content))
        rows: list[str] = []
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            heading_level = _docx_heading_level(paragraph.style.name if paragraph.style else "")
            rows.append(f"{'#' * heading_level} {text}" if heading_level else text)
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    rows.append(" | ".join(cells))
        return "\n".join(rows)
    except Exception:
        return _extract_docx_with_zip(content)


def _docx_heading_level(style_name: str) -> int | None:
    match = re.match(r"^(?:Heading|标题)\s*([1-6])$", style_name.strip(), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _extract_docx_with_zip(content: bytes) -> str:
    try:
        with ZipFile(BytesIO(content)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    except Exception as exc:
        raise KnowledgeParseError("无法解析 docx 文档。") from exc
    parser = _DocxTextExtractor()
    parser.feed(xml)
    return parser.text


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    @property
    def text(self) -> str:
        return "\n".join(part.strip() for part in self._parts if part.strip())

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._parts.append(data)


class _DocxTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    @property
    def text(self) -> str:
        return "\n".join(part.strip() for part in self._parts if part.strip())

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._parts.append(data)
