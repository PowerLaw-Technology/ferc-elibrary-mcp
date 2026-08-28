from __future__ import annotations

from pathlib import Path
from typing import Any

from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.extract.outline import get_document_outline
from ferc_elibrary_mcp.extract.pages import (
    build_page_map_from_pdf,
    build_page_map_from_text,
    extract_full_text,
    load_page_map,
    page_map_to_json,
)
from ferc_elibrary_mcp.store.models import StoredFile, utc_now_iso
from ferc_elibrary_mcp.store.paths import extracted_text_path, pages_json_path
from ferc_elibrary_mcp.store.protocol import DocumentStore


class ExtractionPipeline:
    def __init__(self, store: DocumentStore) -> None:
        self._store = store

    def ensure_extracted(
        self,
        docket: str,
        accession: str,
        filename: str,
        *,
        content_type: str = "",
    ) -> tuple[Path, Path, dict[str, Any]]:
        text_path = extracted_text_path(self._store.root, docket, accession, filename)
        pages_path = pages_json_path(self._store.root, docket, accession, filename)
        if text_path.is_file() and pages_path.is_file():
            meta = load_page_map(pages_path.read_text(encoding="utf-8"))
            return text_path, pages_path, {
                "page_count": len(meta.pages) or None,
                "extracted_char_count": meta.total_chars,
                "extractor": "cached",
                "ocr_used": False,
            }

        file_path = self._store.get(docket, accession, filename)
        if file_path is None:
            raise FileNotFoundError(f"{filename} is not in the store for {accession}")
        body = file_path.read_bytes()
        text, extract_meta = extract_full_text(body, filename, content_type=content_type)
        if filename.lower().endswith(".pdf") or body.startswith(b"%PDF"):
            page_map = build_page_map_from_pdf(body)
            if page_map.total_chars < len(text):
                page_map = build_page_map_from_text(text)
        else:
            page_map = build_page_map_from_text(text)

        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
        pages_path.write_text(page_map_to_json(page_map), encoding="utf-8")
        sync = getattr(self._store, "sync_file", None)
        if callable(sync):
            sync(text_path)
            sync(pages_path)

        manifest = self._store.manifest(docket, accession)
        if manifest is not None:
            stored = next((f for f in manifest.files if f.filename == filename), None)
            if stored is None:
                manifest.files.append(
                    StoredFile(
                        filename=filename,
                        size_bytes=len(body),
                        page_count=extract_meta.get("page_count"),
                        extracted_char_count=len(text),
                        content_type=content_type,
                        fetched_at=utc_now_iso(),
                        extractor=str(extract_meta.get("extractor", "")),
                        ocr_used=bool(extract_meta.get("ocr_used")),
                    )
                )
            else:
                stored.page_count = extract_meta.get("page_count")
                stored.extracted_char_count = len(text)
                stored.extractor = str(extract_meta.get("extractor", stored.extractor))
                stored.ocr_used = bool(extract_meta.get("ocr_used"))
            self._store.save_manifest(docket, accession, manifest)

        return text_path, pages_path, {
            "page_count": extract_meta.get("page_count"),
            "extracted_char_count": len(text),
            "extractor": extract_meta.get("extractor"),
            "ocr_used": extract_meta.get("ocr_used", False),
            "skip_reason": extract_meta.get("skip_reason"),
        }
