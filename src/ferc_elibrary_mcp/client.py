from __future__ import annotations

import asyncio
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.exceptions import (
    ELibraryRequestError,
    FilingNotFoundError,
    RestrictedDocumentError,
)
from ferc_elibrary_mcp.models import (
    DocketFiling,
    DocketSheet,
    DownloadFormat,
    DownloadResult,
    FilingDetail,
    FilingSummary,
    RelatedCollection,
    SearchHit,
    SearchResponse,
    Transmittal,
    file_list_url,
    hit_to_detail,
    hit_to_summary,
)

_UNSAFE_NAME = re.compile(r"[^\w.\- ]+", re.UNICODE)


class ELibraryClient:
    """Thin async client for FERC's undocumented eLibrarywebapi."""

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        download_dir: Path | None = None,
        rate_limit_seconds: float | None = None,
    ) -> None:
        self._http = http
        self._owns_http = http is None
        self._download_dir = Path(download_dir or config.DEFAULT_DOWNLOAD_DIR)
        self._rate_limit = (
            config.RATE_LIMIT_SECONDS if rate_limit_seconds is None else rate_limit_seconds
        )
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> ELibraryClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=config.BASE_URL,
                headers={
                    "User-Agent": config.USER_AGENT,
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(
                    connect=config.CONNECT_TIMEOUT,
                    read=config.SEARCH_TIMEOUT,
                    write=config.CONNECT_TIMEOUT,
                    pool=config.CONNECT_TIMEOUT,
                ),
                follow_redirects=True,
            )
        return self._http

    async def _throttle(self) -> None:
        if self._rate_limit <= 0:
            return
        async with self._lock:
            wait = self._rate_limit - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        content: bytes | str | None = None,
        params: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        await self._throttle()
        client = self._ensure_http()
        try:
            response = await client.request(
                method,
                path,
                json=json,
                content=content,
                params=params,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise ELibraryRequestError(f"eLibrary request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ELibraryRequestError(
                f"eLibrary returned HTTP {response.status_code} for {path}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ELibraryRequestError(
                f"eLibrary returned non-JSON for {path}: {response.text[:200]}"
            ) from exc

    async def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        content: bytes | str | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[bytes, str]:
        await self._throttle()
        client = self._ensure_http()
        try:
            response = await client.request(
                method,
                path,
                json=json,
                content=content,
                params=params,
                timeout=config.DOWNLOAD_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise ELibraryRequestError(f"eLibrary download failed: {exc}") from exc
        if response.status_code >= 400:
            raise ELibraryRequestError(
                f"eLibrary returned HTTP {response.status_code} for {path}"
            )
        content_type = response.headers.get("content-type", "application/octet-stream")
        return response.content, content_type.split(";")[0].strip()

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
    ) -> tuple[SearchResponse, list[FilingSummary]]:
        limit = max(1, min(limit, config.MAX_SEARCH_LIMIT))
        page = max(1, page)
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
        )
        raw = await self._request_json("POST", "Search/AdvancedSearch", json=payload)
        parsed = SearchResponse.model_validate(raw)
        if parsed.error_message:
            raise ELibraryRequestError(str(parsed.error_message))
        return parsed, [hit_to_summary(hit) for hit in parsed.search_hits]

    async def get_filing(self, accession_number: str) -> FilingDetail:
        accession_number = accession_number.strip()
        parsed, _ = await self.search(
            accession_number=accession_number,
            page=1,
            limit=10,
            start_date=None,
            end_date=None,
        )
        for hit in parsed.search_hits:
            if hit.accession_number == accession_number:
                return hit_to_detail(hit)
        if parsed.search_hits:
            return hit_to_detail(parsed.search_hits[0])
        raise FilingNotFoundError(f"No filing found for accession {accession_number}")

    async def list_files(self, accession_number: str) -> FilingDetail:
        return await self.get_filing(accession_number)

    async def get_docket(
        self,
        docket_number: str,
        *,
        subdockets: str = "All",
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 0,
        limit: int = config.DEFAULT_SEARCH_LIMIT,
    ) -> DocketSheet:
        docket_number = docket_number.strip()
        limit = max(1, min(limit, config.MAX_SEARCH_LIMIT))
        page = max(0, page)
        start, end = _docket_date_range(start_date, end_date)
        payload = {
            "dockets": docket_number,
            "subdockets": subdockets or "All",
            "filed_date_beg": start,
            "filed_date_end": end,
            "complete_flag": 0,
            "numHits": limit,
            "pageNumber": page,
        }
        raw = await self._request_json(
            "POST", "Docket/GetSingleDocketSheet", json=payload
        )
        return _parse_docket_sheet(docket_number, raw, page=page, page_size=limit)

    async def download_file(
        self,
        accession_number: str,
        *,
        file_id: str | None = None,
        format: DownloadFormat = "native",
        max_bytes: int = config.MAX_DOWNLOAD_BYTES,
    ) -> DownloadResult:
        filing = await self.get_filing(accession_number)
        if filing.availability.lower() != "public":
            raise RestrictedDocumentError(
                f"Filing {accession_number} is {filing.availability}; "
                "only public documents can be downloaded."
            )

        self._download_dir.mkdir(parents=True, exist_ok=True)
        dest_dir = self._download_dir / _safe_name(accession_number)
        dest_dir.mkdir(parents=True, exist_ok=True)

        if format == "pdf":
            body, content_type = await self._request_bytes(
                "POST",
                "File/DownloadPDF",
                params={"accessionNumber": accession_number},
                content='{serverLocation: ""}',
            )
            filename = f"{accession_number}.pdf"
            return self._write_download(
                dest_dir / filename,
                body,
                content_type or "application/pdf",
                accession_number,
                filename,
                max_bytes=max_bytes,
            )

        transmittal = _select_transmittal(filing, file_id)
        if transmittal.file_size and transmittal.file_size > max_bytes:
            return DownloadResult(
                accession_number=accession_number,
                path="",
                size=transmittal.file_size,
                content_type=transmittal.file_type or "application/octet-stream",
                file_name=transmittal.file_name,
                url=file_list_url(accession_number),
                skipped=True,
                skip_reason=f"File is {transmittal.file_size} bytes, over the {max_bytes} byte cap",
            )

        body, content_type = await self._request_bytes(
            "POST",
            "File/DownloadP8File",
            json={
                "FileType": transmittal.file_type or transmittal.file_format or "",
                "accession": accession_number,
                "fileid": 0,
                "FileIDAll": "",
                "fileidLst": [transmittal.file_id],
                "Islegacy": False,
            },
        )
        filename = _safe_name(transmittal.file_name) or f"{transmittal.file_id}.bin"
        if "." not in filename and transmittal.file_format:
            filename = f"{filename}.{transmittal.file_format.lower()}"
        return self._write_download(
            dest_dir / filename,
            body,
            content_type,
            accession_number,
            filename,
            max_bytes=max_bytes,
        )

    def _write_download(
        self,
        path: Path,
        body: bytes,
        content_type: str,
        accession_number: str,
        file_name: str,
        *,
        max_bytes: int,
    ) -> DownloadResult:
        if len(body) > max_bytes:
            return DownloadResult(
                accession_number=accession_number,
                path="",
                size=len(body),
                content_type=content_type,
                file_name=file_name,
                url=file_list_url(accession_number),
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
        max_dockets: int = config.COLLECT_MAX_DOCKETS,
        max_filings_per_docket: int = config.COLLECT_MAX_FILINGS_PER_DOCKET,
        max_downloads: int = config.COLLECT_MAX_DOWNLOADS,
        max_download_bytes: int = config.MAX_DOWNLOAD_BYTES,
    ) -> RelatedCollection:
        parsed, _summaries = await self.search(
            query=query,
            docket=docket,
            document_type=document_type,
            category=category,
            industry=industry,
            start_date=start_date,
            end_date=end_date,
            page=1,
            limit=config.MAX_SEARCH_LIMIT,
        )
        docket_numbers = _unique_dockets(parsed.search_hits)
        capped = len(docket_numbers) > max_dockets
        selected = docket_numbers[:max_dockets]

        groups: list[DocketSheet] = []
        for number in selected:
            sheet = await self.get_docket(
                number, page=0, limit=max_filings_per_docket
            )
            groups.append(sheet)

        downloads: list[DownloadResult] = []
        if download:
            downloads = await self._download_from_hits(
                parsed.search_hits,
                max_downloads=max_downloads,
                max_bytes=max_download_bytes,
            )

        return RelatedCollection(
            query=query,
            document_type=document_type,
            search_total_hits=parsed.total_hits,
            dockets_returned=len(groups),
            dockets_capped=capped,
            filings_capped_per_docket=max_filings_per_docket,
            groups=groups,
            downloads=downloads,
        )

    async def _download_from_hits(
        self,
        hits: list[SearchHit],
        *,
        max_downloads: int,
        max_bytes: int,
    ) -> list[DownloadResult]:
        results: list[DownloadResult] = []
        for hit in hits:
            if len(results) >= max_downloads:
                break
            if (hit.avail_code or "P").lower() not in config.PUBLIC_AVAIL_CODES:
                continue
            for transmittal in hit.transmittals:
                if len(results) >= max_downloads:
                    break
                if not transmittal.file_id:
                    continue
                result = await self.download_file(
                    hit.accession_number,
                    file_id=transmittal.file_id,
                    format="native",
                    max_bytes=max_bytes,
                )
                results.append(result)
        return results

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
    ) -> dict[str, Any]:
        start, end = _search_date_range(start_date, end_date, accession_number)
        all_dates = bool(accession_number) and start_date is None and end_date is None
        docket_searches: list[dict[str, Any]]
        if docket:
            docket_searches = [
                {"docketNumber": docket.strip(), "subDocketNumbers": []}
            ]
        else:
            docket_searches = [{"docketNumber": None, "subDocketNumbers": []}]

        class_types: list[dict[str, str]] = []
        if document_type:
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
            "searchText": (query or "").strip() or "*",
            "searchFullText": True,
            "searchDescription": True,
            "dateSearches": []
            if all_dates
            else [{"dateType": "filed_date", "startDate": start, "endDate": end}],
            "availability": ["P"] if public_only else None,
            "affiliations": [],
            "categories": categories,
            "libraries": libraries,
            "accessionNumber": accession_number.strip() if accession_number else None,
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
        return payload


def _search_date_range(
    start_date: str | None,
    end_date: str | None,
    accession_number: str | None,
) -> tuple[str, str]:
    today = date.today()
    end = _parse_iso_date(end_date) if end_date else today
    if start_date:
        start = _parse_iso_date(start_date)
    elif accession_number:
        start = date(1960, 1, 1)
    else:
        start = today - timedelta(days=60)
    return start.isoformat(), end.isoformat()


def _docket_date_range(
    start_date: str | None, end_date: str | None
) -> tuple[str, str]:
    today = date.today()
    end = _parse_iso_date(end_date) if end_date else today
    start = _parse_iso_date(start_date) if start_date else date(1960, 1, 1)
    return start.strftime("%m-%d-%Y"), end.strftime("%m-%d-%Y")


def _parse_iso_date(value: str) -> date:
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    for fmt in ("%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date: {value}. Use YYYY-MM-DD.")


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
    docket_number: str, raw: Any, *, page: int, page_size: int
) -> DocketSheet:
    if not isinstance(raw, dict):
        raise ELibraryRequestError("Docket sheet response was not a JSON object")
    page_info = raw.get("Page") or {}
    filings: list[DocketFiling] = []
    applicants: list[str] = []
    for group in raw.get("DataList") or []:
        for doc in group.get("DocumentsItem") or []:
            accession = str(doc.get("accession_no") or "")
            orgs = [str(o) for o in (doc.get("Affiliation_Organization") or []) if o]
            for org in orgs:
                if org not in applicants:
                    applicants.append(org)
            filings.append(
                DocketFiling(
                    accession_number=accession,
                    description=str(doc.get("doc_desc") or ""),
                    category=doc.get("category"),
                    filed_date=str(doc.get("filed_date") or ""),
                    docket=str(doc.get("DOCKET_TEXT") or docket_number),
                    sub_docket=str(doc.get("SUBDOCKET_TEXT") or ""),
                    organizations=orgs,
                    url=file_list_url(accession) if accession else "",
                )
            )
    return DocketSheet(
        docket_number=docket_number,
        total_hits=int(page_info.get("totalHits") or len(filings)),
        page=int(page_info.get("pageNumber") or page),
        page_size=page_size,
        applicants=applicants,
        filings=filings,
    )
