from __future__ import annotations

import io
import re
from typing import Any

from pypdf import PdfReader

from ferc_elibrary_mcp.extract.pages import build_page_map_from_text

_HEADING_RE = re.compile(
    r"^(?:[IVXLC]+\.|[0-9]+(?:\.[0-9]+)*\.?)\s+[A-Z][A-Z0-9 ,\-/&]{4,}$|^[A-Z][A-Z0-9 ,\-/&]{8,}$",
    re.MULTILINE,
)


def get_document_outline(body: bytes, filename: str, extracted_text: str = "") -> dict[str, Any]:
    if filename.lower().endswith(".pdf") or body.startswith(b"%PDF"):
        bookmarks = _pdf_bookmarks(body)
        if bookmarks:
            return {"source": "pdf_bookmarks", "sections": bookmarks}
    text = extracted_text or ""
    sections = _heuristic_sections(text)
    return {"source": "heuristic", "sections": sections}


def _pdf_bookmarks(body: bytes) -> list[dict[str, Any]]:
    reader = PdfReader(io.BytesIO(body), strict=False)
    outline = getattr(reader, "outline", None) or []
    sections: list[dict[str, Any]] = []

    def walk(items: list[Any], level: int = 1) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item, level + 1)
                continue
            title = getattr(item, "title", None) or str(item)
            page = None
            try:
                page_obj = reader.get_destination_page_number(item)
                page = page_obj + 1 if page_obj is not None else None
            except Exception:
                page = None
            sections.append({"title": str(title).strip(), "page": page, "level": level})

    walk(outline)
    return sections


def _heuristic_sections(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    page_map = build_page_map_from_text(text)
    sections: list[dict[str, Any]] = []
    for match in _HEADING_RE.finditer(text):
        title = match.group(0).strip()
        char_start = match.start()
        page = 1
        for entry in page_map.pages:
            if char_start >= entry["start_char"]:
                page = entry["page"]
        sections.append({"title": title, "page": page, "char_start": char_start, "level": 1})
    return sections[:200]
