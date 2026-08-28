from __future__ import annotations

import asyncio
import io
import mimetypes
import re
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.exceptions import (
    ELibraryRequestError,
    FilingNotFoundError,
    RestrictedDocumentError,
)
from ferc_elibrary_mcp.models import (
    BundleDownloadResult,
    DateField,
    DateRangeResolution,
    DocketFiling,
    DocketSheet,
    DownloadFormat,
    DownloadResult,
    FilingDetail,
    FilingSummary,
    MatchMode,
    RelatedCollection,
    SearchHit,
    SearchResponse,
    SearchScope,
    SkippedAccession,
    SortOrder,
    TextExtractionResult,
    Transmittal,
    file_list_url,
    hit_to_detail,
    hit_to_summary,
)
from ferc_elibrary_mcp.ferc.client import FercClient
from ferc_elibrary_mcp.services.documents import DocumentService, SyncService
from ferc_elibrary_mcp.store import create_store
from ferc_elibrary_mcp.store.protocol import DocumentStore

_UNSAFE_NAME = re.compile(r"[^\w.\- ]+", re.UNICODE)
_ACCESSION_ZIP_PREFIX = re.compile(r"^(\d{8}-\d{4})_(.+)$")


class ELibraryClient:
    """Async client for FERC eLibrary with cache-first document storage."""

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        download_dir: Path | None = None,
        rate_limit_seconds: float | None = None,
        store: DocumentStore | None = None,
    ) -> None:
        rps = None if rate_limit_seconds is None else (1.0 / rate_limit_seconds if rate_limit_seconds > 0 else 0.0)
        self._ferc = FercClient(http=http, rps=rps)
        self._http = http
        self._owns_http = http is None
        root = Path(download_dir) if download_dir is not None else config.resolve_store_root()
        self._store = store or create_store(root)
        self._documents = DocumentService(self._store, self)
        self._sync = SyncService(self._store, self, self._documents)
        # Legacy alias used by older tests/config.
        self._download_dir = self._store.root

    async def aclose(self) -> None:
        await self._ferc.aclose()
        self._http = None

    async def __aenter__(self) -> ELibraryClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    @property
    def store(self) -> DocumentStore:
        return self._store

    def _ensure_http(self) -> httpx.AsyncClient:
        return self._ferc._ensure_http()

    async def _send(self, *args: Any, **kwargs: Any) -> httpx.Response:
        return await self._ferc._send(*args, **kwargs)

    async def _request_json(self, *args: Any, **kwargs: Any) -> Any:
        return await self._ferc.request_json(*args, **kwargs)

    async def _request_bytes(self, *args: Any, **kwargs: Any) -> tuple[bytes, str | None]:
        body, name, _headers = await self._ferc.request_bytes(*args, **kwargs)
        return body, name

    def _select_transmittal(self, filing: FilingDetail, file_id: str | None) -> Transmittal:
        return _select_transmittal(filing, file_id)

    def _safe_name(self, name: str) -> str:
        return _safe_name(name)

    async def search(
        self,
        *,
        query: str | None = None,
        docket: str | None = None,
        accession_number: str | None = None,
        document_type: str | None = None,
        category: str | None = None,
        industry: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        limit: int = config.DEFAULT_SEARCH_LIMIT,
        public_only: bool = True,
        match: MatchMode = "phrase",
        search_in: SearchScope = "both",
        date_field: DateField = "filed",
    ) -> tuple[SearchResponse, list[FilingSummary], DateRangeResolution]:
        limit = max(1, min(limit, config.MAX_SEARCH_LIMIT))
        page = max(1, page)
        dates = resolve_date_range(
            start_date,
            end_date,
            docket=docket,
            accession_number=accession_number,
            date_field=date_field,
        )
        payload = self._build_search_payload(
            query=query,
            docket=docket,
            accession_number=accession_number,
            document_type=document_type,
            category=category,
            industry=industry,
            start_date=start_date,
            end_date=end_date,
            page=page,
            limit=limit,
            public_only=public_only,
            match=match,
            search_in=search_in,
            dates=dates,
        )
        raw = await self._request_json("POST", "Search/AdvancedSearch", json=payload)
        parsed = SearchResponse.model_validate(raw)
        if parsed.error_message or parsed.success is False:
            raise ELibraryRequestError(
                parsed.error_message or "eLibrary search returned success=false"
            )
        summaries = [hit_to_summary(hit) for hit in parsed.search_hits]
        for summary in summaries:
            docket = summary.docket_numbers[0] if summary.docket_numbers else summary.accession_number
            summary.__dict__["cached"] = self._store.accession_cached(docket, summary.accession_number)
        return parsed, summaries, dates

    async def get_filing(self, accession_number: str) -> FilingDetail:
        accession_number = accession_number.strip()
        parsed, _, _dates = await self.search(
            accession_number=accession_number,
            page=1,
            limit=10,
            start_date=None,
            end_date=None,
        )
        hit = _hit_for_accession(parsed, accession_number)
        if hit is None:
            # Public search omits privileged/CEII/protected filings, so a miss
            # is not "the accession does not exist." Retry without the public
            # filter so callers can distinguish restricted from absent.
            parsed, _, _dates = await self.search(
                accession_number=accession_number,
                page=1,
                limit=10,
                start_date=None,
                end_date=None,
                public_only=False,
            )
            hit = _hit_for_accession(parsed, accession_number)
        if hit is None:
            raise FilingNotFoundError(
                f"Accession {accession_number} is not in public eLibrary search. "
                "If it exists it is likely privileged, protected, or CEII and "
                "cannot be downloaded."
            )
        detail = hit_to_detail(hit)
        docket = detail.docket_numbers[0] if detail.docket_numbers else accession_number
        detail.__dict__["cached"] = self._store.accession_cached(docket, accession_number)
        for item in detail.files:
            manifest = self._store.manifest(docket, accession_number)
            stored = None
            if manifest:
                stored = next((f for f in manifest.files if f.filename == item.file_name), None)
            item.__dict__["cached"] = self._store.exists(docket, accession_number, item.file_name)
            item.__dict__["page_count"] = stored.page_count if stored else None
            item.__dict__["extracted_char_count"] = stored.extracted_char_count if stored else None
        return detail

    async def list_files(self, accession_number: str) -> FilingDetail:
        return await self.get_filing(accession_number)

    async def get_docket(
        self,
        docket_number: str,
        *,
        subdockets: str = "All",
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        limit: int = config.DEFAULT_SEARCH_LIMIT,
        date_field: DateField = "filed",
        sort_order: SortOrder = "oldest_first",
    ) -> DocketSheet:
        docket_number = docket_number.strip()
        limit = max(1, min(limit, config.MAX_SEARCH_LIMIT))
        # page 0 used to be the first page; accept it as 1 rather than erroring.
        page = max(1, page)
        dates = resolve_date_range(
            start_date,
            end_date,
            docket=docket_number,
            date_field=date_field,
        )
        parent, extracted_sub = split_docket_number(docket_number)
        if subdockets and subdockets != "All":
            sub = subdockets
        elif extracted_sub and subdockets == "All":
            # Parent + All returns the related sheet; parent-000 as the docket id
            # is treated as a literal and comes back empty.
            sub = "All"
        else:
            sub = subdockets or "All"

        # The sheet filters server-side on filed date only, and it reports every
        # issued_date as the .NET null sentinel, so an issuance window cannot be
        # answered from its own rows. Resolve that set through search, which
        # carries real issuance dates and filters on them server-side.
        issued_window = dates.field == "issued" and (dates.start or dates.end)
        keep: set[str] | None = None
        if issued_window:
            keep = await self._accessions_in_issued_window(
                docket_number, dates.start, dates.end
            )
        window_start, window_end = _docket_window(
            None if issued_window else dates.start,
            None if issued_window else dates.end,
        )
        payload = {
            "dockets": parent,
            "subdockets": sub,
            "filed_date_beg": window_start,
            "filed_date_end": window_end,
            "complete_flag": 0,
            # numHits/pageNumber do not slice reliably: rows-per-page exceed the
            # limit and later pages overlap. Ask for everything and page here.
            "numHits": config.DOCKET_SHEET_FETCH_LIMIT,
            "pageNumber": 0,
        }
        raw = await self._request_json(
            "POST", "Docket/GetSingleDocketSheet", json=payload
        )
        if issued_window:
            dates = dates.model_copy(update={"filtered_client_side": True})
        sheet = _parse_docket_sheet(
            parent,
            raw,
            page=page,
            page_size=limit,
            dates=dates,
            sort_order=sort_order,
            keep_accessions=keep,
        )
        for filing in sheet.filings:
            docket = filing.docket_numbers[0] if filing.docket_numbers else parent
            filing.__dict__["cached"] = self._store.accession_cached(docket, filing.accession_number)
        return sheet

    async def _accessions_in_issued_window(
        self, docket: str, start: str | None, end: str | None
    ) -> set[str]:
        """Accessions in a docket whose issuance date falls in the window."""
        found: set[str] = set()
        page = 1
        while page <= config.MAX_CROSS_REFERENCE_PAGES:
            _parsed, hits, _dates = await self.search(
                docket=docket,
                start_date=start,
                end_date=end,
                date_field="issued",
                page=page,
                limit=config.MAX_SEARCH_LIMIT,
                # Match the docket sheet, which cannot filter on availability.
                public_only=False,
            )
            if not hits:
                break
            found.update(hit.accession_number for hit in hits)
            page += 1
        return found

    async def download_file(
        self,
        accession_number: str,
        *,
        file_id: str | None = None,
        format: DownloadFormat = "native",
        max_bytes: int = config.MAX_DOWNLOAD_BYTES,
    ) -> DownloadResult:
        return await self._documents.ensure_file(
            accession_number,
            file_id=file_id,
            format=format,
            max_bytes=max_bytes,
        )

    async def _download_file_to_store(
        self,
        filing: FilingDetail,
        *,
        docket: str,
        file_id: str | None = None,
        format: DownloadFormat = "native",
        max_bytes: int = config.MAX_DOWNLOAD_BYTES,
    ) -> DownloadResult:
        accession_number = filing.accession_number
        if filing.availability.lower() != "public":
            raise RestrictedDocumentError(
                f"Filing {accession_number} is {filing.availability}; "
                "only public documents can be downloaded."
            )

        if format == "pdf":
            body, suggested = await self._request_bytes(
                "POST",
                "File/DownloadPDF",
                params={"accessionNumber": accession_number},
                content='{serverLocation: ""}',
            )
            filename = _safe_name(suggested or "") or f"{accession_number}.pdf"
            return self._write_download_to_store(
                docket,
                accession_number,
                filename,
                body,
                max_bytes=max_bytes,
                is_bundle=True,
                file_id=file_id or "",
            )

        if format == "zip":
            body, suggested = await self._request_bytes(
                "POST",
                "File/DownloadP8File",
                json={
                    "FileType": "",
                    "accession": accession_number,
                    "fileid": 0,
                    "FileIDAll": "",
                    "fileidLst": [f.file_id for f in filing.files if f.file_id],
                    "Islegacy": False,
                },
            )
            filename = _safe_name(suggested or "") or f"{accession_number}.zip"
            body, filename, is_bundle, expected_size = _normalize_zip_download(
                body,
                filename,
                filing=filing,
            )
            return self._write_download_to_store(
                docket,
                accession_number,
                filename,
                body,
                max_bytes=max_bytes,
                is_bundle=is_bundle,
                expected_size=expected_size,
                file_id=file_id or "",
            )

        transmittal = _select_transmittal(filing, file_id)
        if transmittal.file_size and transmittal.file_size > max_bytes:
            return DownloadResult(
                accession_number=accession_number,
                path="",
                size=transmittal.file_size,
                content_type=guess_content_type(b"", transmittal.file_name),
                file_name=transmittal.file_name,
                url=file_list_url(accession_number),
                expected_size=transmittal.file_size,
                skipped=True,
                skip_reason=f"File is {transmittal.file_size} bytes, over the {max_bytes} byte cap",
            )

        body, suggested = await self._request_bytes(
            "POST",
            "File/DownloadP8File",
            json={
                "FileType": transmittal.file_type or transmittal.file_format or "",
                "accession": "",
                "fileid": 0,
                "FileIDAll": "",
                "fileidLst": [transmittal.file_id],
                "Islegacy": False,
            },
        )
        filename = (
            _safe_name(suggested or "")
            or _safe_name(transmittal.file_name)
            or f"{transmittal.file_id}.bin"
        )
        if "." not in filename and transmittal.file_format:
            filename = f"{filename}.{transmittal.file_format.lower()}"
        return self._write_download_to_store(
            docket,
            accession_number,
            filename,
            body,
            max_bytes=max_bytes,
            expected_size=transmittal.file_size or None,
            file_id=transmittal.file_id,
        )

    async def get_filing_text(
        self,
        accession_number: str,
        *,
        file_id: str | None = None,
        max_chars: int = config.DEFAULT_EXTRACT_CHARS,
        max_bytes: int = config.MAX_EXTRACT_FILE_BYTES,
    ) -> TextExtractionResult:
        filing = await self.get_filing(accession_number)
        filename = None
        if file_id:
            for item in filing.files:
                if item.file_id == file_id:
                    filename = item.file_name
                    break
        elif filing.files:
            filename = filing.files[0].file_name
        if not filename:
            return TextExtractionResult(
                accession_number=accession_number,
                file_name="",
                file_id=file_id,
                path="",
                content_type="",
                text="",
                char_count=0,
                truncated=False,
                extractor="none",
                url=file_list_url(accession_number),
                skipped=True,
                skip_reason="No file found on accession",
            )
        read = await self._documents.read_document_for_filing(
            filing,
            filename,
            max_chars=max_chars,
        )
        return TextExtractionResult(
            accession_number=accession_number,
            file_name=filename,
            file_id=file_id,
            path=read.get("path", ""),
            content_type="",
            text=read.get("text", ""),
            char_count=int(read.get("char_count", 0)),
            truncated=bool(read.get("truncated")),
            page_count=read.get("page_count"),
            extractor=str(read.get("extractor", "read_document")),
            url=file_list_url(accession_number),
            skipped=False,
            skip_reason=(
                f"Deprecated: use read_document. Total chars: {read.get('total_chars')}. "
                f"Hint: Use get_document_outline or search_within_document, then read_document "
                f"with pages/char_range."
                if read.get("truncated")
                else "Deprecated: use read_document instead."
            ),
        )

    async def read_document(
        self,
        accession_number: str,
        filename: str,
        *,
        pages: list[int] | None = None,
        char_start: int | None = None,
        char_end: int | None = None,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        return await self._documents.read_document(
            accession_number,
            filename,
            pages=pages,
            char_start=char_start,
            char_end=char_end,
            max_chars=max_chars,
        )

    async def search_within_document(
        self,
        accession_number: str,
        filename: str,
        query: str,
        *,
        max_hits: int = 10,
    ) -> dict[str, Any]:
        return await self._documents.search_within_document(
            accession_number,
            filename,
            query,
            max_hits=max_hits,
        )

    async def get_document_outline(
        self,
        accession_number: str,
        filename: str,
    ) -> dict[str, Any]:
        return await self._documents.get_document_outline(accession_number, filename)

    async def sync_docket(self, docket_number: str) -> dict[str, Any]:
        return await self._sync.sync_docket(docket_number)

    def cache_status(
        self,
        *,
        docket: str | None = None,
        accession: str | None = None,
    ) -> dict[str, Any]:
        return self._store.cache_status(docket=docket, accession=accession).model_dump()

    async def download_bundle(
        self,
        *,
        accession_numbers: list[str] | None = None,
        file_ids: list[str] | None = None,
        docket: str | None = None,
        organize_by_accession: bool = True,
        max_bytes: int = config.MAX_BUNDLE_BYTES,
        max_files: int = config.MAX_BUNDLE_FILES,
    ) -> BundleDownloadResult:
        """Zip many public files in one eLibrary request.

        eLibrary's Zip & Download endpoint accepts a list of file IDs across
        accessions and returns a single archive. That is far cheaper than
        calling download_file once per attachment (one metadata fetch + one
        download + rate-limit gap each). FERC names members
        ``{accession}_{filename}``; when organize_by_accession is true we
        rewrite them to ``{accession}/{filename}`` folders.

        Privileged, protected, and CEII accessions — including those omitted
        from public search — are listed in ``skipped_accessions`` and do not
        abort the rest of the bundle.
        """
        accessions = [a.strip() for a in (accession_numbers or []) if a and a.strip()]
        ids = [i.strip() for i in (file_ids or []) if i and i.strip()]
        docket = (docket or "").strip() or None
        if not accessions and not ids and not docket:
            raise ValueError(
                "download_bundle requires accession_numbers, file_ids, and/or docket"
            )

        selected: list[str] = []
        seen: set[str] = set()
        accession_set: set[str] = set()
        skipped_accessions: list[SkippedAccession] = []
        expected_total = 0
        files_capped = False

        def _take(file_id: str, accession: str = "", size: int = 0) -> bool:
            nonlocal files_capped, expected_total
            if not file_id or file_id in seen:
                return True
            if len(selected) >= max_files:
                files_capped = True
                return False
            seen.add(file_id)
            selected.append(file_id)
            if accession:
                accession_set.add(accession)
            expected_total += max(size, 0)
            return True

        for file_id in ids:
            if not _take(file_id):
                break

        for accession in accessions:
            if files_capped:
                break
            try:
                filing = await self.get_filing(accession)
            except FilingNotFoundError:
                # Public search (and often unrestricted search) omit privileged
                # / CEII / protected filings. Skip them rather than aborting
                # the rest of the bundle.
                skipped_accessions.append(
                    SkippedAccession(
                        accession_number=accession,
                        reason=(
                            "Not found in eLibrary. Check for a typo; if the "
                            "accession exists it may be privileged, protected, "
                            "or CEII and cannot be downloaded."
                        ),
                        category="not_found",
                    )
                )
                continue
            if filing.availability.lower() != "public":
                skipped_accessions.append(
                    SkippedAccession(
                        accession_number=accession,
                        reason=(
                            f"{filing.availability}; only public documents can "
                            "be downloaded."
                        ),
                        category="restricted",
                    )
                )
                continue
            for item in filing.files:
                if not _take(item.file_id, accession, item.file_size):
                    break

        if docket and not files_capped:
            await self._collect_docket_file_ids(
                docket, take=_take, already_capped=lambda: files_capped
            )

        if not selected:
            return BundleDownloadResult(
                path="",
                size=0,
                file_name="",
                file_count=0,
                accession_numbers=sorted(accession_set),
                file_ids=[],
                organized_by_accession=organize_by_accession,
                files_capped=files_capped,
                skipped_accessions=skipped_accessions,
                skipped=True,
                skip_reason="No public files matched the request",
            )

        if expected_total and expected_total > max_bytes:
            return BundleDownloadResult(
                path="",
                size=expected_total,
                file_name="",
                file_count=len(selected),
                accession_numbers=sorted(accession_set),
                file_ids=selected,
                organized_by_accession=organize_by_accession,
                expected_size=expected_total,
                files_capped=files_capped,
                skipped_accessions=skipped_accessions,
                skipped=True,
                skip_reason=(
                    f"Estimated payload is {expected_total} bytes, over the "
                    f"{max_bytes} byte cap"
                ),
            )

        body, suggested = await self._request_bytes(
            "POST",
            "File/DownloadP8File",
            json={
                "FileType": "",
                "accession": "",
                "fileid": 0,
                "FileIDAll": "",
                "fileidLst": selected,
                "Islegacy": False,
            },
            timeout=config.BUNDLE_DOWNLOAD_TIMEOUT,
        )

        if organize_by_accession:
            body = folderize_zip(body)

        zip_accessions = accessions_in_zip(body) or sorted(accession_set)
        self._download_dir.mkdir(parents=True, exist_ok=True)
        dest_dir = self._download_dir / "bundles"
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = _safe_name(suggested or "") or _bundle_filename(
            zip_accessions, len(selected)
        )
        if not filename.lower().endswith(".zip"):
            filename = f"{filename}.zip"
        path = dest_dir / filename

        if len(body) > max_bytes:
            return BundleDownloadResult(
                path="",
                size=len(body),
                file_name=filename,
                file_count=len(selected),
                accession_numbers=zip_accessions,
                file_ids=selected,
                organized_by_accession=organize_by_accession,
                expected_size=expected_total or None,
                files_capped=files_capped,
                skipped_accessions=skipped_accessions,
                skipped=True,
                skip_reason=(
                    f"Downloaded payload is {len(body)} bytes, over the "
                    f"{max_bytes} byte cap"
                ),
            )

        path.write_bytes(body)
        return BundleDownloadResult(
            path=str(path.resolve()),
            size=len(body),
            file_name=filename,
            file_count=len(selected),
            accession_numbers=zip_accessions,
            file_ids=selected,
            organized_by_accession=organize_by_accession,
            expected_size=expected_total or None,
            files_capped=files_capped,
            skipped_accessions=skipped_accessions,
        )

    async def _collect_docket_file_ids(
        self,
        docket: str,
        *,
        take,
        already_capped,
    ) -> None:
        """Pull public file IDs for a docket via search (has avail codes)."""
        page = 1
        while not already_capped() and page <= config.MAX_CROSS_REFERENCE_PAGES:
            parsed, _, _ = await self.search(
                docket=docket, page=page, limit=config.MAX_SEARCH_LIMIT
            )
            if not parsed.search_hits:
                break
            for hit in parsed.search_hits:
                if (hit.avail_code or "P").lower() not in config.PUBLIC_AVAIL_CODES:
                    continue
                for transmittal in hit.transmittals:
                    if not take(
                        transmittal.file_id,
                        hit.accession_number,
                        transmittal.file_size,
                    ):
                        return
            if page * config.MAX_SEARCH_LIMIT >= parsed.total_hits:
                break
            page += 1

    def _write_download_to_store(
        self,
        docket: str,
        accession_number: str,
        file_name: str,
        body: bytes,
        *,
        max_bytes: int,
        is_bundle: bool = False,
        expected_size: int | None = None,
        file_id: str = "",
    ) -> DownloadResult:
        content_type = guess_content_type(body, file_name)
        bundle = is_bundle or content_type == "application/zip"
        if len(body) > max_bytes:
            return DownloadResult(
                accession_number=accession_number,
                path="",
                size=len(body),
                content_type=content_type,
                file_name=file_name,
                url=file_list_url(accession_number),
                is_bundle=bundle,
                expected_size=expected_size,
                skipped=True,
                skip_reason=f"Downloaded payload is {len(body)} bytes, over the {max_bytes} byte cap",
            )
        path = self._store.put(
            docket,
            accession_number,
            file_name,
            body,
            file_id=file_id,
            content_type=content_type,
        )
        return DownloadResult(
            accession_number=accession_number,
            path=str(path.resolve()),
            size=len(body),
            content_type=content_type,
            file_name=file_name,
            url=file_list_url(accession_number),
            is_bundle=bundle,
            expected_size=expected_size,
            size_matches_metadata=(
                None if expected_size is None else len(body) == expected_size
            ),
        )

    def _write_download(
        self,
        path: Path,
        body: bytes,
        accession_number: str,
        file_name: str,
        *,
        max_bytes: int,
        is_bundle: bool = False,
        expected_size: int | None = None,
    ) -> DownloadResult:
        content_type = guess_content_type(body, file_name)
        bundle = is_bundle or content_type == "application/zip"
        if len(body) > max_bytes:
            return DownloadResult(
                accession_number=accession_number,
                path="",
                size=len(body),
                content_type=content_type,
                file_name=file_name,
                url=file_list_url(accession_number),
                is_bundle=bundle,
                expected_size=expected_size,
                skipped=True,
                skip_reason=f"Downloaded payload is {len(body)} bytes, over the {max_bytes} byte cap",
            )
        path.write_bytes(body)
        return DownloadResult(
            accession_number=accession_number,
            path=str(path.resolve()),
            size=len(body),
            content_type=content_type,
            file_name=file_name,
            url=file_list_url(accession_number),
            is_bundle=bundle,
            expected_size=expected_size,
            size_matches_metadata=(
                None if expected_size is None else len(body) == expected_size
            ),
        )

    async def collect_related(
        self,
        *,
        query: str | None = None,
        document_type: str | None = None,
        docket: str | None = None,
        category: str | None = None,
        industry: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        download: bool = False,
        match: MatchMode = "phrase",
        search_in: SearchScope = "both",
        date_field: DateField = "filed",
        max_dockets: int = config.COLLECT_MAX_DOCKETS,
        max_filings_per_docket: int = config.COLLECT_MAX_FILINGS_PER_DOCKET,
        max_downloads: int = config.COLLECT_MAX_DOWNLOADS,
        max_download_bytes: int = config.MAX_BUNDLE_BYTES,
    ) -> RelatedCollection:
        parsed, _summaries, dates = await self.search(
            query=query,
            docket=docket,
            document_type=document_type,
            category=category,
            industry=industry,
            start_date=start_date,
            end_date=end_date,
            page=1,
            limit=config.MAX_SEARCH_LIMIT,
            match=match,
            search_in=search_in,
            date_field=date_field,
        )
        docket_numbers = _unique_dockets(parsed.search_hits)
        capped = len(docket_numbers) > max_dockets
        selected = docket_numbers[:max_dockets]

        groups: list[DocketSheet] = []
        for number in selected:
            sheet = await self.get_docket(
                number, page=1, limit=max_filings_per_docket
            )
            groups.append(sheet)

        downloads: list[DownloadResult] = []
        bundle: BundleDownloadResult | None = None
        if download:
            bundle = await self._bundle_from_hits(
                parsed.search_hits,
                max_files=max_downloads,
                max_bytes=max_download_bytes,
            )
            if bundle and not bundle.skipped and bundle.path:
                downloads = [
                    DownloadResult(
                        accession_number=",".join(bundle.accession_numbers[:3])
                        + ("…" if len(bundle.accession_numbers) > 3 else ""),
                        path=bundle.path,
                        size=bundle.size,
                        content_type=bundle.content_type,
                        file_name=bundle.file_name,
                        url="",
                        is_bundle=True,
                        expected_size=bundle.expected_size,
                    )
                ]

        return RelatedCollection(
            query=query,
            document_type=document_type,
            date_range_applied={"start": dates.start, "end": dates.end},
            date_range_source=dates.source,
            date_field_applied=dates.field,
            results_may_be_date_limited=dates.may_be_date_limited,
            search_total_hits=parsed.total_hits,
            dockets_returned=len(groups),
            dockets_capped=capped,
            filings_capped_per_docket=max_filings_per_docket,
            groups=groups,
            downloads=downloads,
            bundle=bundle,
        )

    async def _bundle_from_hits(
        self,
        hits: list[SearchHit],
        *,
        max_files: int,
        max_bytes: int,
    ) -> BundleDownloadResult:
        """One Zip & Download for public files already present on search hits."""
        file_ids: list[str] = []
        seen: set[str] = set()
        for hit in hits:
            if (hit.avail_code or "P").lower() not in config.PUBLIC_AVAIL_CODES:
                continue
            for transmittal in hit.transmittals:
                if not transmittal.file_id or transmittal.file_id in seen:
                    continue
                seen.add(transmittal.file_id)
                file_ids.append(transmittal.file_id)
                if len(file_ids) >= max_files:
                    break
            if len(file_ids) >= max_files:
                break
        if not file_ids:
            return BundleDownloadResult(
                path="",
                size=0,
                file_name="",
                file_count=0,
                skipped=True,
                skip_reason="No public files on the search hits",
            )
        return await self.download_bundle(
            file_ids=file_ids,
            max_files=max_files,
            max_bytes=max_bytes,
        )

    def _build_search_payload(
        self,
        *,
        query: str | None,
        docket: str | None,
        accession_number: str | None,
        document_type: str | None,
        category: str | None,
        industry: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        limit: int,
        public_only: bool,
        match: MatchMode = "phrase",
        search_in: SearchScope = "both",
        dates: DateRangeResolution | None = None,
    ) -> dict[str, Any]:
        if dates is None:
            dates = resolve_date_range(
                start_date,
                end_date,
                docket=docket,
                accession_number=accession_number,
            )
        all_dates = dates.source == "none"
        docket_searches: list[dict[str, Any]] = []
        if docket and docket.strip():
            docket_searches = [
                {"docketNumber": docket.strip(), "subDocketNumbers": []}
            ]

        class_types: list[dict[str, str]] = []
        if document_type and document_type.strip():
            class_types = [
                {"documentClass": document_type.strip(), "documentType": ""}
            ]

        categories = []
        if category:
            key = category.strip().lower()
            categories = [config.CATEGORY_ALIASES.get(key, category.strip())]

        libraries = []
        if industry:
            key = industry.strip().lower()
            libraries = [config.INDUSTRY_ALIASES.get(key, industry.strip())]

        payload: dict[str, Any] = {
            "searchText": build_search_text(query, match),
            "searchFullText": search_in in ("full_text", "both"),
            "searchDescription": search_in in ("description", "both"),
            "dateSearches": []
            if all_dates
            else [
                {
                    "dateType": config.DATE_FIELD_TYPES[dates.field],
                    "startDate": dates.start,
                    "endDate": dates.end,
                }
            ],
            "availability": ["P"] if public_only else [],
            "affiliations": [],
            "categories": categories,
            "libraries": libraries,
            "eFiling": False,
            "docketSearches": docket_searches,
            "resultsPerPage": limit,
            "curPage": page,
            "classTypes": class_types,
            "sortBy": "",
            "groupBy": "NONE",
            "idolResultID": "",
            "allDates": all_dates,
        }
        if accession_number and accession_number.strip():
            payload["accessionNumber"] = accession_number.strip()
        return payload


def resolve_date_range(
    start_date: str | None,
    end_date: str | None,
    *,
    docket: str | None = None,
    accession_number: str | None = None,
    date_field: DateField = "filed",
) -> DateRangeResolution:
    """Decide which date window to apply, and record why.

    A docket or accession number is an intentional scope, so layering a 60-day
    window on top of it silently hides the bulk of a proceeding. The default
    window only applies to open-ended queries, and the choice is always
    reported back so a caller can never mistake a filtered count for a total.
    """
    if date_field not in config.DATE_FIELD_TYPES:
        raise ValueError(
            f"Unrecognized date_field: {date_field}. "
            f"Use one of {sorted(config.DATE_FIELD_TYPES)}."
        )

    if start_date or end_date:
        today = date.today()
        start = _parse_iso_date(start_date) if start_date else date(1960, 1, 1)
        end = _parse_iso_date(end_date) if end_date else today
        return DateRangeResolution(
            start=start.isoformat(),
            end=end.isoformat(),
            source="explicit",
            field=date_field,
        )

    if (docket and docket.strip()) or (accession_number and accession_number.strip()):
        return DateRangeResolution(source="none", field=date_field)

    today = date.today()
    start = today - timedelta(days=config.DEFAULT_LOOKBACK_DAYS)
    return DateRangeResolution(
        start=start.isoformat(),
        end=today.isoformat(),
        source="default_60_day",
        field=date_field,
    )


def _docket_window(start: str | None, end: str | None) -> tuple[str, str]:
    """Render an already-resolved range in the MM-DD-YYYY the sheet expects."""
    parsed_start = _parse_iso_date(start) if start else date(1960, 1, 1)
    parsed_end = _parse_iso_date(end) if end else date.today()
    return parsed_start.strftime("%m-%d-%Y"), parsed_end.strftime("%m-%d-%Y")


def build_search_text(query: str | None, match: MatchMode) -> str:
    """Turn a plain query into eLibrary search syntax.

    eLibrary treats bare multi-word text as independent terms, which buries the
    filings that actually contain the phrase. Quoting collapses it to a phrase
    search; "all" requires every term.
    """
    text = (query or "").strip()
    if not text:
        return "*"
    if match == "any":
        return text
    # Respect syntax the caller wrote themselves.
    if '"' in text or any(
        op in text for op in (" AND ", " OR ", " NOT ", " NEAR ")
    ):
        return text
    terms = text.split()
    if len(terms) == 1:
        return text
    if match == "all":
        return " AND ".join(terms)
    return f'"{text}"'


def guess_content_type(body: bytes, file_name: str = "") -> str:
    """eLibrary always claims octet-stream, so sniff the bytes, then the name.

    OOXML files (.docx/.xlsx/.pptx) are ZIP containers, so the PK magic must
    not win over a known office extension — otherwise a bare Word file is
    reported as application/zip and is_bundle=true.
    """
    suffix = Path(file_name).suffix.lower()
    if suffix in config.OOXML_EXTENSIONS:
        return config.EXTENSION_CONTENT_TYPES.get(
            suffix,
            mimetypes.guess_type(file_name)[0] or "application/octet-stream",
        )
    for magic, content_type in config.MAGIC_CONTENT_TYPES:
        if body.startswith(magic):
            return content_type
    if suffix in config.EXTENSION_CONTENT_TYPES:
        return config.EXTENSION_CONTENT_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(file_name or "file")
    return guessed or "application/octet-stream"


def _zip_file_members(body: bytes) -> list[zipfile.ZipInfo] | None:
    if body[:2] != b"PK":
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            return [info for info in zf.infolist() if not info.is_dir()]
    except zipfile.BadZipFile:
        return None


def _is_ooxml_package(body: bytes) -> bool:
    """True when body is an Office Open XML package, not an eLibrary zip."""
    members = _zip_file_members(body)
    if not members:
        return False
    return any(
        Path(info.filename).name == "[Content_Types].xml" for info in members
    )


def unwrap_single_member_zip(body: bytes) -> tuple[bytes, str] | None:
    """If body is an eLibrary zip with exactly one file, return that file.

    OOXML packages are also zips; leave them alone so .docx stays intact.
    """
    if _is_ooxml_package(body):
        return None
    members = _zip_file_members(body)
    if members is None or len(members) != 1:
        return None
    info = members[0]
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            payload = zf.read(info)
    except zipfile.BadZipFile:
        return None
    name = Path(info.filename).name
    match = _ACCESSION_ZIP_PREFIX.match(name)
    if match:
        name = match.group(2)
    return payload, name


def _normalize_zip_download(
    body: bytes,
    filename: str,
    *,
    filing: FilingDetail,
) -> tuple[bytes, str, bool, int | None]:
    """Return (body, filename, is_bundle, expected_size) after single-file unwrap.

    format=zip always *requests* a zip, but for a one-file accession eLibrary
    may return a one-member archive (or a bare OOXML file). Save the real
    document and refresh metadata so content_type / is_bundle / expected_size
    describe what landed on disk.
    """
    single = filing.files[0] if len(filing.files) == 1 else None
    single_size = single.file_size or None if single else None

    unwrapped = unwrap_single_member_zip(body)
    if unwrapped is not None:
        payload, inner_name = unwrapped
        name = _safe_name(inner_name) or (
            _safe_name(single.file_name) if single and single.file_name else filename
        )
        return payload, name, False, single_size

    if _is_ooxml_package(body):
        name = filename
        suffix = Path(name).suffix.lower()
        if suffix not in config.OOXML_EXTENSIONS:
            if single and single.file_name:
                name = _safe_name(single.file_name) or name
            elif not name.lower().endswith(".docx"):
                name = f"{Path(name).stem}.docx"
        return body, name, False, single_size

    expected = sum((f.file_size or 0) for f in filing.files) or None
    return body, filename, True, expected


def _filename_from_disposition(disposition: str) -> str | None:
    if not disposition:
        return None
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
    if not match:
        return None
    return unquote(match.group(1)).strip() or None


def split_docket_number(number: str) -> tuple[str, str | None]:
    """Split ER26-3178-000 into (ER26-3178, 000). Leave CP21-470 unchanged."""
    number = number.strip()
    parts = number.split("-")
    if len(parts) >= 3 and parts[-1].isdigit():
        return "-".join(parts[:-1]), parts[-1]
    return number, None


def _ferc_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        text = response.text[:200]
        return f": {text}" if text else ""
    if isinstance(data, dict) and data.get("errorMessage"):
        return f": {data['errorMessage']}"
    return ""


def _parse_iso_date(value: str) -> date:
    value = value.strip()
    # The docket sheet returns ISO datetimes ("2026-08-06T00:00:00") where
    # search returns MM/DD/YYYY.
    if "T" in value:
        value = value.split("T", 1)[0]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    for fmt in ("%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"Unrecognized date: {value}. Use YYYY-MM-DD or MM/DD/YYYY."
    )


def folderize_zip(body: bytes) -> bytes:
    """Rewrite FERC's flat ``accession_filename`` members into folders.

    eLibrary's Zip & Download names every member ``{YYYYMMDD-NNNN}_{name}``.
    That is readable but awkward for browsing; this turns it into
    ``{YYYYMMDD-NNNN}/{name}`` without re-compressing payloads (PDFs rarely
    shrink).
    """
    if body[:2] != b"PK":
        return body
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(body)) as src, zipfile.ZipFile(
        out, "w", compression=zipfile.ZIP_STORED
    ) as dst:
        for info in src.infolist():
            name = info.filename
            match = _ACCESSION_ZIP_PREFIX.match(name)
            new_name = f"{match.group(1)}/{match.group(2)}" if match else name
            # Preserve directory entries if FERC ever sends them.
            if name.endswith("/"):
                dst.writestr(new_name if new_name.endswith("/") else new_name + "/", b"")
                continue
            dst.writestr(new_name, src.read(info.filename))
    return out.getvalue()


def accessions_in_zip(body: bytes) -> list[str]:
    """Distinct accession numbers found in zip member names."""
    if body[:2] != b"PK":
        return []
    found: list[str] = []
    seen: set[str] = set()
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        for name in zf.namelist():
            match = _ACCESSION_ZIP_PREFIX.match(name) or re.match(
                r"^(\d{8}-\d{4})/", name
            )
            if not match:
                continue
            accession = match.group(1)
            if accession not in seen:
                seen.add(accession)
                found.append(accession)
    return found


def _bundle_filename(accessions: list[str], file_count: int) -> str:
    if len(accessions) == 1:
        return f"{accessions[0]}_{file_count}files.zip"
    if accessions:
        return f"bundle_{accessions[0]}_plus{len(accessions) - 1}_{file_count}files.zip"
    return f"bundle_{file_count}files.zip"


def _safe_name(name: str) -> str:
    cleaned = _UNSAFE_NAME.sub("_", name).strip(" ._")
    return cleaned[:180]


def _unique_dockets(hits: list[SearchHit]) -> list[str]:
    seen: list[str] = []
    for hit in hits:
        for number in hit.docket_numbers:
            if number and number not in seen:
                seen.append(number)
    return seen


def _hit_for_accession(
    parsed: SearchResponse, accession_number: str
) -> SearchHit | None:
    """Return the hit for this accession, or the first hit, or None."""
    for hit in parsed.search_hits:
        if hit.accession_number == accession_number:
            return hit
    if parsed.search_hits:
        return parsed.search_hits[0]
    return None


def _select_transmittal(filing: FilingDetail, file_id: str | None) -> Transmittal:
    if not filing.files:
        raise FilingNotFoundError(
            f"Filing {filing.accession_number} has no attached files"
        )
    if file_id:
        for item in filing.files:
            if item.file_id == file_id:
                return Transmittal(
                    fileId=item.file_id,
                    fileName=item.file_name,
                    fileType=item.file_type,
                    fileFormat=item.file_type,
                    fileSize=item.file_size,
                    fileDesc=item.description,
                )
        raise FilingNotFoundError(
            f"File id {file_id} was not found on accession {filing.accession_number}"
        )
    first = filing.files[0]
    return Transmittal(
        fileId=first.file_id,
        fileName=first.file_name,
        fileType=first.file_type,
        fileFormat=first.file_type,
        fileSize=first.file_size,
        fileDesc=first.description,
    )


def _parse_docket_sheet(
    docket_number: str,
    raw: Any,
    *,
    page: int,
    page_size: int,
    dates: DateRangeResolution,
    sort_order: SortOrder = "oldest_first",
    keep_accessions: set[str] | None = None,
) -> DocketSheet:
    """Collapse docket-association rows into one row per accession.

    GetSingleDocketSheet returns one row per (accession, subdocket) pair and a
    totalHits that counts associations, so a filing captioned to -000, -001 and
    -002 appears three times and is counted three times. Callers want filings,
    so rows are merged on accession number and every subdocket is preserved in
    docket_numbers. Reporting FERC's count directly overstated EL25-49 as 380
    against 312 actual filings.
    """
    if not isinstance(raw, dict):
        raise ELibraryRequestError("Docket sheet response was not a JSON object")

    merged: dict[str, DocketFiling] = {}
    applicants: list[str] = []
    subdockets_seen: set[str] = set()
    for group in raw.get("DataList") or []:
        for doc in group.get("DocumentsItem") or []:
            accession = str(doc.get("accession_no") or "")
            if not accession:
                continue
            orgs = [str(o) for o in (doc.get("Affiliation_Organization") or []) if o]
            for org in orgs:
                if org not in applicants:
                    applicants.append(org)
            parent = str(doc.get("DOCKET_TEXT") or docket_number)
            sub = str(doc.get("SUBDOCKET_TEXT") or "")
            if sub:
                subdockets_seen.add(sub)
            qualified = f"{parent}-{sub}" if sub else parent

            existing = merged.get(accession)
            if existing is not None:
                if qualified not in existing.docket_numbers:
                    existing.docket_numbers.append(qualified)
                if sub and sub not in existing.sub_dockets:
                    existing.sub_dockets.append(sub)
                for org in orgs:
                    if org not in existing.organizations:
                        existing.organizations.append(org)
                continue

            merged[accession] = DocketFiling(
                accession_number=accession,
                description=str(doc.get("doc_desc") or ""),
                category=doc.get("category"),
                filed_date=_display_date(str(doc.get("filed_date") or "")),
                issued_date=_display_date(str(doc.get("issued_date") or "")),
                docket=parent,
                sub_docket=sub,
                docket_numbers=[qualified],
                sub_dockets=[sub] if sub else [],
                organizations=orgs,
                url=file_list_url(accession),
            )

    filings = list(merged.values())
    if keep_accessions is not None:
        filings = [f for f in filings if f.accession_number in keep_accessions]

    filings.sort(
        key=lambda f: (_sort_key(f.filed_date), f.accession_number),
        reverse=sort_order == "newest_first",
    )

    total = len(filings)
    start = (page - 1) * page_size
    return DocketSheet(
        docket_number=docket_number,
        total_hits=total,
        page=page,
        page_size=page_size,
        page_base=1,
        count_basis="distinct_accession",
        includes_subdockets=sorted(subdockets_seen),
        availability_scope="all",
        sort_order=sort_order,
        applicants=applicants,
        filings=filings[start : start + page_size],
        **{
            key: value
            for key, value in dates.as_envelope().items()
            if key != "date_range_applied"
        },
        date_range_applied={"start": dates.start, "end": dates.end},
    )


def _sort_key(value: str) -> date:
    try:
        return _parse_iso_date(value)
    except ValueError:
        return date(1900, 1, 1)


def _display_date(value: str) -> str:
    """Normalize a docket-sheet date to the MM/DD/YYYY that search returns.

    The sheet uses .NET DateTime.MinValue ("0001-01-01") as its null, so that
    sentinel becomes an empty string rather than a date in year 1.
    """
    value = (value or "").strip()
    if not value:
        return ""
    try:
        parsed = _parse_iso_date(value)
    except ValueError:
        return value
    if parsed.year <= 1:
        return ""
    return parsed.strftime("%m/%d/%Y")


