"""Extract plain text from public eLibrary attachments for MCP tool responses.

Downloads alone only write a path on disk. Agents (Claude Desktop sandboxes,
remote connectors, claude.ai) cannot open that path, so summarization tools
need the text returned in the tool payload — capped so a long order cannot
flood the context window.
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from pypdf import PdfReader

from ferc_elibrary_mcp import config

_WHITESPACE_RE = re.compile(r"[ \t]+\n")


def extract_text(
    body: bytes,
    file_name: str = "",
    *,
    content_type: str = "",
    max_chars: int = config.DEFAULT_EXTRACT_CHARS,
) -> tuple[str, dict[str, object]]:
    """Return (text, meta) for a downloaded attachment.

    meta always includes extractor, truncated, char_count, and may include
    page_count or skip_reason when extraction is unsupported.
    """
    max_chars = max(1, min(int(max_chars), config.MAX_EXTRACT_CHARS))
    kind = _detect_kind(body, file_name, content_type)
    if kind == "pdf":
        text, pages = _extract_pdf(body)
        return _clip(text, max_chars, extractor="pypdf", page_count=pages)
    if kind == "docx":
        text = _extract_docx(body)
        return _clip(text, max_chars, extractor="docx")
    if kind == "plain":
        text = body.decode("utf-8", errors="replace")
        return _clip(text, max_chars, extractor="plain")
    if kind == "zip":
        return "", {
            "extractor": "unsupported",
            "truncated": False,
            "char_count": 0,
            "page_count": None,
            "skip_reason": (
                "Attachment is a multi-file zip. Download members separately "
                "or call get_filing_text with a specific file_id."
            ),
        }
    return "", {
        "extractor": "unsupported",
        "truncated": False,
        "char_count": 0,
        "page_count": None,
        "skip_reason": (
            f"No text extractor for {file_name or content_type or 'unknown type'}. "
            "PDF, DOCX, and plain text are supported."
        ),
    }


def _detect_kind(body: bytes, file_name: str, content_type: str) -> str:
    suffix = Path(file_name).suffix.lower()
    ctype = (content_type or "").lower()
    if body.startswith(b"%PDF") or suffix == ".pdf" or "pdf" in ctype:
        return "pdf"
    if suffix in config.OOXML_EXTENSIONS or _looks_like_docx(body):
        if suffix in {".xlsx", ".pptx", ".xlsm", ".pptm"}:
            return "unsupported"
        return "docx"
    if suffix in {".txt", ".csv", ".log", ".md"} or ctype.startswith("text/"):
        return "plain"
    if body[:2] == b"PK":
        return "zip"
    # Heuristic: mostly printable ASCII/UTF-8.
    sample = body[:2000]
    if sample and sum(32 <= b < 127 or b in (9, 10, 13) for b in sample) / len(sample) > 0.85:
        return "plain"
    return "unsupported"


def _looks_like_docx(body: bytes) -> bool:
    if body[:2] != b"PK":
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return False
    return any(Path(n).name == "[Content_Types].xml" for n in names) and any(
        n.startswith("word/") for n in names
    )


def _extract_pdf(body: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(body), strict=False)
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    text = _normalize("\n\n".join(parts))
    return text, len(reader.pages)


def _extract_docx(body: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        try:
            xml_bytes = zf.read("word/document.xml")
        except KeyError as exc:
            raise ValueError("DOCX is missing word/document.xml") from exc
    root = ET.fromstring(xml_bytes)
    paragraphs: list[str] = []
    for para in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        runs = [
            node.text or ""
            for node in para.iter(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
            )
        ]
        line = "".join(runs).strip()
        if line:
            paragraphs.append(line)
    return _normalize("\n\n".join(paragraphs))


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE_RE.sub("\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clip(
    text: str,
    max_chars: int,
    *,
    extractor: str,
    page_count: int | None = None,
) -> tuple[str, dict[str, object]]:
    truncated = len(text) > max_chars
    if truncated:
        text = text[: max_chars - 1].rstrip() + "…"
    return text, {
        "extractor": extractor,
        "truncated": truncated,
        "char_count": len(text),
        "page_count": page_count,
        "skip_reason": None,
    }
