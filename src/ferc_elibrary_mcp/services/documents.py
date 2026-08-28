from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.extract.outline import get_document_outline
from ferc_elibrary_mcp.extract.pages import load_page_map
from ferc_elibrary_mcp.extract.pipeline import ExtractionPipeline
from ferc_elibrary_mcp.models import (
    DownloadFormat,
    DownloadResult,
    FilingDetail,
    file_list_url,
)
from ferc_elibrary_mcp.store.models import DocketIndex, DocketIndexEntry, utc_now_iso
from ferc_elibrary_mcp.store.protocol import DocumentStore


class DocumentService:
    """Cache-first document storage and bounded reads."""

    def __init__(self, store: DocumentStore, client: Any) -> None:
        self._store = store
        self._client = client
        self._extract = ExtractionPipeline(store)

    @property
    def store(self) -> DocumentStore:
        return self._store

    def primary_docket(self, filing: FilingDetail) -> str:
        if filing.docket_numbers:
            return filing.docket_numbers[0]
        return filing.accession_number

    def is_cached(self, docket: str, accession: str, filename: str | None = None) -> bool:
        if filename:
            return self._store.exists(docket, accession, filename)
        return self._store.accession_cached(docket, accession)

    async def ensure_file(
        self,
        accession: str,
        *,
        file_id: str | None = None,
        format: DownloadFormat = "native",
        max_bytes: int = config.MAX_DOWNLOAD_BYTES,
    ) -> DownloadResult:
        if file_id is None and format == "native":
            cached_files = self._cached_download_for_accession(accession, file_id=None)
            if cached_files is not None:
                return cached_files

        filing = await self._client.get_filing(accession)
        docket = self.primary_docket(filing)
        transmittal = self._client._select_transmittal(filing, file_id)
        filename = self._client._safe_name(transmittal.file_name) or f"{transmittal.file_id}.bin"

        cached = self._store.get(docket, accession, filename)
        if cached is not None:
            manifest = self._store.manifest(docket, accession)
            stored = None
            if manifest:
                stored = next((f for f in manifest.files if f.filename == filename), None)
            return DownloadResult(
                accession_number=accession,
                path=str(cached.resolve()),
                size=cached.stat().st_size,
                content_type=stored.content_type if stored else "",
                file_name=filename,
                url=file_list_url(accession),
                expected_size=stored.size_bytes if stored else None,
                size_matches_metadata=True,
            )

        download = await self._client._download_file_to_store(
            filing,
            docket=docket,
            file_id=file_id,
            format=format,
            max_bytes=max_bytes,
        )
        return download

    def _cached_download_for_accession(
        self,
        accession: str,
        *,
        file_id: str | None,
    ) -> DownloadResult | None:
        dockets_root = self._store.root / "dockets"
        if not dockets_root.is_dir():
            legacy = self._store.root / accession
            if legacy.is_dir():
                files = [p for p in legacy.iterdir() if p.is_file()]
                if files:
                    path = files[0]
                    return DownloadResult(
                        accession_number=accession,
                        path=str(path.resolve()),
                        size=path.stat().st_size,
                        content_type="",
                        file_name=path.name,
                        url=file_list_url(accession),
                    )
            return None
        for docket_dir in dockets_root.iterdir():
            acc_dir = docket_dir / accession
            if not acc_dir.is_dir():
                continue
            manifest = self._store.manifest(docket_dir.name, accession)
            if manifest and manifest.files:
                target = manifest.files[0]
                if file_id:
                    match = next((f for f in manifest.files if f.file_id == file_id), None)
                    if match:
                        target = match
                path = self._store.get(docket_dir.name, accession, target.filename)
                if path is not None:
                    return DownloadResult(
                        accession_number=accession,
                        path=str(path.resolve()),
                        size=path.stat().st_size,
                        content_type=target.content_type,
                        file_name=target.filename,
                        url=file_list_url(accession),
                        expected_size=target.size_bytes,
                        size_matches_metadata=True,
                    )
        return None

    def _resolve_docket(self, accession: str) -> str | None:
        dockets_root = self._store.root / "dockets"
        if dockets_root.is_dir():
            for docket_dir in dockets_root.iterdir():
                if (docket_dir / accession).is_dir():
                    return docket_dir.name
        legacy = self._store.root / accession
        if legacy.is_dir():
            return accession
        return None

    async def read_document_for_filing(
        self,
        filing: FilingDetail,
        filename: str,
        *,
        pages: list[int] | None = None,
        char_start: int | None = None,
        char_end: int | None = None,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        accession = filing.accession_number
        docket = self.primary_docket(filing)
        safe_name = self._client._safe_name(filename)
        if not self._store.exists(docket, accession, safe_name):
            await self._client._download_file_to_store(
                filing,
                docket=docket,
                file_id=self._file_id_for_name(filing, filename),
            )
        return await self._read_extracted(
            docket,
            accession,
            safe_name,
            filing=filing,
            pages=pages,
            char_start=char_start,
            char_end=char_end,
            max_chars=max_chars,
        )

    async def _read_extracted(
        self,
        docket: str,
        accession: str,
        filename: str,
        *,
        filing: FilingDetail | None = None,
        pages: list[int] | None = None,
        char_start: int | None = None,
        char_end: int | None = None,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        text_path, pages_path, meta = self._extract.ensure_extracted(
            docket,
            accession,
            filename,
            content_type=self._content_type_for_name(filing, filename) if filing else "",
        )
        text = text_path.read_text(encoding="utf-8")
        page_map = load_page_map(pages_path.read_text(encoding="utf-8"))
        total_chars = len(text)
        cap = max(1, min(int(max_chars or config.MAX_READ_CHARS), config.MAX_EXTRACT_CHARS))

        if pages:
            selected = []
            for entry in page_map.pages:
                if entry["page"] in pages:
                    selected.append(text[entry["start_char"] : entry["end_char"]])
            slice_text = "\n\n".join(selected)
            start = page_map.pages[min(pages) - 1]["start_char"] if page_map.pages else 0
        elif char_start is not None or char_end is not None:
            start = max(0, char_start or 0)
            end = char_end if char_end is not None else total_chars
            slice_text = text[start:end]
        else:
            start = 0
            slice_text = text

        truncated = len(slice_text) > cap
        if truncated:
            slice_text = slice_text[: cap - 1].rstrip() + "…"
        next_char = None
        next_page = None
        if truncated:
            next_char = start + len(slice_text)
            for entry in page_map.pages:
                if entry["start_char"] >= next_char:
                    next_page = entry["page"]
                    break

        return {
            "accession_number": accession,
            "filename": filename,
            "text": slice_text,
            "char_count": len(slice_text),
            "total_chars": total_chars,
            "page_count": meta.get("page_count"),
            "truncated": truncated,
            "next_char_start": next_char,
            "next_page": next_page,
            "path": str(text_path),
            "extractor": meta.get("extractor"),
        }

    async def read_document(
        self,
        accession: str,
        filename: str,
        *,
        pages: list[int] | None = None,
        char_start: int | None = None,
        char_end: int | None = None,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        docket = self._resolve_docket(accession)
        filing = None
        if docket is None or not self._store.exists(docket, accession, filename):
            filing = await self._client.get_filing(accession)
            docket = self.primary_docket(filing)
            safe_name = self._client._safe_name(filename)
            if not self._store.exists(docket, accession, safe_name):
                await self._client._download_file_to_store(
                    filing,
                    docket=docket,
                    file_id=self._file_id_for_name(filing, filename),
                )
            filename = safe_name
        return await self._read_extracted(
            docket,
            accession,
            filename,
            filing=filing,
            pages=pages,
            char_start=char_start,
            char_end=char_end,
            max_chars=max_chars,
        )

    async def search_within_document(
        self,
        accession: str,
        filename: str,
        query: str,
        *,
        max_hits: int = 10,
    ) -> dict[str, Any]:
        filing = await self._client.get_filing(accession)
        docket = self.primary_docket(filing)
        await self.ensure_file(accession, file_id=self._file_id_for_name(filing, filename))
        text_path, pages_path, _meta = self._extract.ensure_extracted(
            docket,
            accession,
            filename,
            content_type=self._content_type_for_name(filing, filename),
        )
        text = text_path.read_text(encoding="utf-8")
        page_map = load_page_map(pages_path.read_text(encoding="utf-8"))
        needle = query.strip()
        if not needle:
            return {"hits": [], "query": query, "total_chars": len(text)}
        lower = text.lower()
        target = needle.lower()
        hits: list[dict[str, Any]] = []
        start = 0
        while len(hits) < max_hits:
            index = lower.find(target, start)
            if index < 0:
                break
            end = index + len(needle)
            page = 1
            for entry in page_map.pages:
                if index >= entry["start_char"]:
                    page = entry["page"]
            snippet_start = max(0, index - 80)
            snippet_end = min(len(text), end + 80)
            hits.append(
                {
                    "page": page,
                    "char_start": index,
                    "char_end": end,
                    "snippet": text[snippet_start:snippet_end],
                }
            )
            start = end
        return {"hits": hits, "query": query, "total_chars": len(text)}

    async def get_document_outline(self, accession: str, filename: str) -> dict[str, Any]:
        filing = await self._client.get_filing(accession)
        docket = self.primary_docket(filing)
        await self.ensure_file(accession, file_id=self._file_id_for_name(filing, filename))
        file_path = self._store.get(docket, accession, filename)
        if file_path is None:
            raise FileNotFoundError(filename)
        text_path, _pages_path, _meta = self._extract.ensure_extracted(
            docket,
            accession,
            filename,
            content_type=self._content_type_for_name(filing, filename),
        )
        extracted = text_path.read_text(encoding="utf-8") if text_path.is_file() else ""
        return get_document_outline(file_path.read_bytes(), filename, extracted)

    def _file_id_for_name(self, filing: FilingDetail, filename: str) -> str | None:
        for item in filing.files:
            if item.file_name == filename:
                return item.file_id
        return None

    def _content_type_for_name(self, filing: FilingDetail | None, filename: str) -> str:
        if filing is None:
            path = Path(filename)
        else:
            path = Path(filename)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return "application/pdf"
        if suffix == ".docx":
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return ""


class SyncService:
    def __init__(self, store: DocumentStore, client: Any, documents: DocumentService) -> None:
        self._store = store
        self._client = client
        self._documents = documents

    async def sync_docket(self, docket_number: str) -> dict[str, Any]:
        sheet = await self._client.get_docket(docket_number, page=1, limit=5000)
        index = self._store.docket_index(docket_number) or DocketIndex(docket_number=docket_number)
        known = {entry.accession_number for entry in index.accessions}
        new_accessions: list[str] = []
        already_cached: list[str] = []
        skipped: list[dict[str, str]] = []

        for filing in sheet.filings:
            accession = filing.accession_number
            primary = filing.docket_numbers[0] if filing.docket_numbers else docket_number
            if self._store.accession_cached(primary, accession):
                already_cached.append(accession)
                continue
            try:
                detail = await self._client.get_filing(accession)
                if detail.availability.lower() != "public":
                    skipped.append({"accession_number": accession, "reason": detail.availability})
                    continue
                if not detail.files:
                    skipped.append({"accession_number": accession, "reason": "no files"})
                    continue
                first = detail.files[0]
                await self._documents.ensure_file(accession, file_id=first.file_id)
                new_accessions.append(accession)
                entry = DocketIndexEntry(
                    accession_number=accession,
                    filed_date=filing.filed_date,
                    description=filing.description[:200],
                )
                if accession not in known:
                    index.accessions.append(entry)
                    known.add(accession)
            except Exception as exc:
                skipped.append({"accession_number": accession, "reason": str(exc)})

        if index.accessions:
            dates = [a.filed_date for a in index.accessions if a.filed_date]
            index.latest_filing_date = max(dates) if dates else index.latest_filing_date
        self._store.update_docket_index(docket_number, index)

        return {
            "docket_number": docket_number,
            "new": new_accessions,
            "already_cached": already_cached,
            "skipped": skipped,
            "fetched_count": len(new_accessions),
        }
