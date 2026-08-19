from __future__ import annotations

import asyncio
import mimetypes
import re
import time
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

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        content: bytes | str | None = None,
        params: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Send one request, retrying the proxy errors eLibrary throws at random."""
        client = self._ensure_http()
        last_status: int | None = None
        for attempt in range(config.MAX_RETRIES):
            await self._throttle()
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
                if attempt == config.MAX_RETRIES - 1:
                    raise ELibraryRequestError(
                        f"eLibrary request failed: {exc}"
                    ) from exc
                await asyncio.sleep(config.RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue

            if response.status_code not in config.RETRY_STATUS_CODES:
                return response

            last_status = response.status_code
            if attempt < config.MAX_RETRIES - 1:
                await asyncio.sleep(config.RETRY_BACKOFF_SECONDS * (attempt + 1))

        raise ELibraryRequestError(
            f"eLibrary returned HTTP {last_status} for {path} after "
            f"{config.MAX_RETRIES} attempts"
        )

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
        response = await self._send(
            method, path, json=json, content=content, params=params, timeout=timeout
        )

        if response.status_code >= 400:
            detail = _ferc_error_detail(response)
            raise ELibraryRequestError(
                f"eLibrary returned HTTP {response.status_code} for {path}{detail}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ELibraryRequestError(
                f"eLibrary returned non-JSON for {path}: {response.text[:200]}"
            ) from exc
        return payload

    async def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        content: bytes | str | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[bytes, str | None]:
        """Return the body and the server-suggested filename, if any."""
        response = await self._send(
            method,
            path,
            json=json,
            content=content,
            params=params,
            timeout=config.DOWNLOAD_TIMEOUT,
        )
        if response.status_code >= 400:
            detail = _ferc_error_detail(response)
            raise ELibraryRequestError(
                f"eLibrary returned HTTP {response.status_code} for {path}{detail}"
            )
        disposition = response.headers.get("content-disposition", "")
        return response.content, _filename_from_disposition(disposition)

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
            match=match,
            search_in=search_in,
        )
        raw = await self._request_json("POST", "Search/AdvancedSearch", json=payload)
        parsed = SearchResponse.model_validate(raw)
        if parsed.error_message or parsed.success is False:
            raise ELibraryRequestError(
                parsed.error_message or "eLibrary search returned success=false"
            )
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
        parent, extracted_sub = split_docket_number(docket_number)
        if subdockets and subdockets != "All":
            sub = subdockets
        elif extracted_sub and subdockets == "All":
            # Parent + All returns the related sheet; parent-000 as the docket id
            # is treated as a literal and comes back empty.
            sub = "All"
        else:
            sub = subdockets or "All"
        payload = {
            "dockets": parent,
            "subdockets": sub,
            "filed_date_beg": start,
            "filed_date_end": end,
            "complete_flag": 0,
            "numHits": limit,
            "pageNumber": page,
        }
        raw = await self._request_json(
            "POST", "Docket/GetSingleDocketSheet", json=payload
        )
        return _parse_docket_sheet(parent, raw, page=page, page_size=limit)

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
            body, suggested = await self._request_bytes(
                "POST",
                "File/DownloadPDF",
                params={"accessionNumber": accession_number},
                content='{serverLocation: ""}',
            )
            filename = _safe_name(suggested or "") or f"{accession_number}.pdf"
            return self._write_download(
                dest_dir / filename,
                body,
                accession_number,
                filename,
                max_bytes=max_bytes,
                is_bundle=True,
            )

        if format == "zip":
            # A non-empty "accession" makes eLibrary zip every file on the
            # accession instead of serving the one requested.
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
            return self._write_download(
                dest_dir / filename,
                body,
                accession_number,
                filename,
                max_bytes=max_bytes,
                is_bundle=True,
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
        return self._write_download(
            dest_dir / filename,
            body,
            accession_number,
            filename,
            max_bytes=max_bytes,
            expected_size=transmittal.file_size or None,
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
            match=match,
            search_in=search_in,
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
        match: MatchMode = "phrase",
        search_in: SearchScope = "both",
    ) -> dict[str, Any]:
        start, end = _search_date_range(start_date, end_date, accession_number)
        all_dates = bool(accession_number) and start_date is None and end_date is None
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
            else [{"dateType": "filed_date", "startDate": start, "endDate": end}],
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
    """eLibrary always claims octet-stream, so sniff the bytes, then the name."""
    for magic, content_type in config.MAGIC_CONTENT_TYPES:
        if body.startswith(magic):
            return content_type
    suffix = Path(file_name).suffix.lower()
    if suffix in config.EXTENSION_CONTENT_TYPES:
        return config.EXTENSION_CONTENT_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(file_name or "file")
    return guessed or "application/octet-stream"


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
