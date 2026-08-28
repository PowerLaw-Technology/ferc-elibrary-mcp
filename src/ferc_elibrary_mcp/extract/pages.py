from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from typing import Any

from pypdf import PdfReader

from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.textextract import _detect_kind, _extract_docx, _normalize


@dataclass
class PageMap:
    pages: list[dict[str, int]]
    total_chars: int


def build_page_map_from_pdf(body: bytes) -> PageMap:
    reader = PdfReader(io.BytesIO(body), strict=False)
    pages: list[dict[str, int]] = []
    offset = 0
    for index, page in enumerate(reader.pages, start=1):
        text = _normalize(page.extract_text() or "")
        start = offset
        if text:
            if pages and pages[-1]["end_char"] == start:
                pages[-1]["text"] = pages[-1].get("text", "") + "\n\n" + text
            else:
                pages.append({"page": index, "start_char": start, "end_char": start + len(text)})
                offset = start + len(text)
                if index > 1:
                    offset += 2
        else:
            pages.append({"page": index, "start_char": start, "end_char": start})
    clean = [{"page": p["page"], "start_char": p["start_char"], "end_char": p["end_char"]} for p in pages]
    total = clean[-1]["end_char"] if clean else 0
    return PageMap(pages=clean, total_chars=total)


def build_page_map_from_text(text: str) -> PageMap:
    if not text:
        return PageMap(pages=[], total_chars=0)
    return PageMap(pages=[{"page": 1, "start_char": 0, "end_char": len(text)}], total_chars=len(text))


def extract_full_text(body: bytes, filename: str, *, content_type: str = "") -> tuple[str, dict[str, Any]]:
    kind = _detect_kind(body, filename, content_type)
    if kind == "pdf":
        reader = PdfReader(io.BytesIO(body), strict=False)
        parts = [_normalize(page.extract_text() or "") for page in reader.pages]
        text = _normalize("\n\n".join(parts))
        return text, {"extractor": "pypdf", "page_count": len(reader.pages), "ocr_used": False}
    if kind == "docx":
        text = _extract_docx(body)
        return text, {"extractor": "docx", "page_count": None, "ocr_used": False}
    if kind == "plain":
        text = body.decode("utf-8", errors="replace")
        return _normalize(text), {"extractor": "plain", "page_count": None, "ocr_used": False}
    return "", {"extractor": "unsupported", "page_count": None, "ocr_used": False, "skip_reason": f"Unsupported type for {filename}"}


def page_map_to_json(page_map: PageMap) -> str:
    return json.dumps({"pages": page_map.pages, "total_chars": page_map.total_chars}, indent=2)


def load_page_map(raw: str) -> PageMap:
    data = json.loads(raw)
    return PageMap(pages=data.get("pages", []), total_chars=int(data.get("total_chars", 0)))
