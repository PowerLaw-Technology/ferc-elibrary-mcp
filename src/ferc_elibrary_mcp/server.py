from __future__ import annotations

import signal
import threading
import time
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.client import ELibraryClient
from ferc_elibrary_mcp.exceptions import ELibraryError
from ferc_elibrary_mcp.models import (
    DateField,
    DownloadFormat,
    MatchMode,
    SearchScope,
    SortOrder,
)

mcp = FastMCP(
    name="ferc-elibrary",
    instructions=(
        "Search the public FERC eLibrary for dockets and filings. "
        "Use search_filings for keywords or document types, get_docket for a docket "
        "sheet of related filings, get_filing/list_files for one accession, "
        "sync_docket to incrementally cache a docket, cache_status to inspect the store, "
        "download_file to cache public files (never returns text), "
        "download_bundle to Zip & Download many public files across accessions, "
        "and collect_related to search a term and gather related docket filings. "
        "To read document substance, never request full text. Use get_document_outline "
        "and search_within_document to locate sections, then read_document with explicit "
        "page or character bounds. get_filing_text is deprecated and bounded. "
        "Only public documents can be downloaded. Dates use YYYY-MM-DD. "
        "Prefer pagination over huge dumps."
    ),
)

_client: ELibraryClient | None = None


def get_client() -> ELibraryClient:
    global _client
    if _client is None:
        _client = ELibraryClient()
    return _client


def set_client(client: ELibraryClient | None) -> None:
    global _client
    _client = client


def _dump(model: Any) -> Any:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model


def _tool_error(exc: Exception) -> None:
    if isinstance(exc, ELibraryError):
        raise ToolError(str(exc)) from exc
    if isinstance(exc, ValueError):
        raise ToolError(str(exc)) from exc
    raise ToolError(f"Unexpected eLibrary error: {exc}") from exc


@mcp.tool
async def search_filings(
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
    match: MatchMode = "phrase",
    search_in: SearchScope = "both",
    date_field: DateField = "filed",
) -> dict[str, Any]:
    """Search public FERC eLibrary filings. Public documents only.

    Use for keyword/term search, docket prefix (CP, ER11-4046), accession numbers,
    or document types such as Order/Opinion, Comments/Protest, or
    Application/Petition/Request.

    Date defaulting: when docket or accession_number is supplied, no date filter
    is applied and the whole proceeding is searched. For an open-ended query with
    no dates, the last 60 days is used to keep the result set manageable.
    Every response reports date_range_applied, date_range_source
    (explicit/default_60_day/none), and results_may_be_date_limited, so check
    those before treating total_hits as a complete count.

    date_field selects which date start_date and end_date filter on. Use
    "issued" when computing deadlines: FPA 313(a) rehearing and most
    Commission-set comment and compliance clocks run from issuance, not from the
    filed date, and the two differ. Orders are generally best searched by
    issuance.

    match controls how a multi-word query is interpreted. "phrase" (default)
    requires the exact phrase and is what you want when looking for a named
    agreement or document. "all" requires every term anywhere. "any" is FERC's
    loose term matching, which returns high volume and low precision.

    search_in controls where the query is matched. "both" (default) covers
    descriptions and full document text. "description" is far more precise
    because it matches the filing title rather than any passing mention deep in
    an attachment. Use it when a phrase search still returns too much noise.

    You may also pass eLibrary syntax directly (quotes, AND, OR, NOT, NEAR);
    it is forwarded unchanged.
    """
    try:
        parsed, hits, dates = await get_client().search(
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
            match=match,
            search_in=search_in,
            date_field=date_field,
        )
    except Exception as exc:
        _tool_error(exc)
        raise
    return {
        "total_hits": parsed.total_hits,
        "page": page,
        "limit": min(max(limit, 1), config.MAX_SEARCH_LIMIT),
        "match": match,
        "search_in": search_in,
        **dates.as_envelope(),
        "hits": [_dump(hit) for hit in hits],
    }


@mcp.tool
async def get_docket(
    docket_number: str,
    subdockets: str = "All",
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    limit: int = config.DEFAULT_SEARCH_LIMIT,
    date_field: DateField = "filed",
    sort_order: SortOrder = "oldest_first",
) -> dict[str, Any]:
    """Return the docket sheet: related filings, applicants, and accession numbers.

    Docket numbers look like CP21-470, ER11-4046, or P-15056-000. Subdockets can
    be All or a comma-separated list such as 000,001.

    page is 1-indexed, matching search_filings. page=0 is accepted as page 1.

    One row per filing: eLibrary returns one row per docket association, so a
    pleading captioned to -000, -001 and -002 arrives three times. Rows are
    merged on accession number and every association is listed in
    docket_numbers, so total_hits counts filings you can actually retrieve.
    count_basis reports distinct_accession to make that explicit.

    Scope differs from search_filings in one way worth knowing: the docket sheet
    carries no availability code, so it cannot filter by availability and
    reports availability_scope "all". search_filings is public-only by default,
    so a docket sheet may list a few privileged filings that search omits.

    sort_order defaults to oldest_first, the chronological order of a docket
    sheet. search_filings returns newest first. Pass newest_first to match it.

    date_field and the date envelope behave as in search_filings. Since a docket
    number is always supplied, no 60-day default is ever applied here. An
    issued-date window is applied to rows after retrieval, reported via
    date_field_filtered_client_side.
    """
    try:
        sheet = await get_client().get_docket(
            docket_number,
            subdockets=subdockets,
            start_date=start_date,
            end_date=end_date,
            page=page,
            limit=limit,
            date_field=date_field,
            sort_order=sort_order,
        )
    except Exception as exc:
        _tool_error(exc)
        raise
    return _dump(sheet)


@mcp.tool
async def get_filing(accession_number: str) -> dict[str, Any]:
    """Fetch metadata for one filing by accession number (YYYYMMDD-NNNN).

    has_nonpublic_counterpart signals that a sealed, protected, or CEII version
    likely exists on the same accession, which is what you would move for access
    to under 18 C.F.R. 388.113. It is inferred from filer naming convention
    ("PUBLIC" or "REDACTED" in a file name), so nonpublic_counterpart_basis
    reports it as file_naming_convention rather than authoritative metadata. No
    protected content is ever returned.
    """
    try:
        filing = await get_client().get_filing(accession_number)
    except Exception as exc:
        _tool_error(exc)
        raise
    return _dump(filing)


@mcp.tool
async def list_files(accession_number: str) -> dict[str, Any]:
    """List files attached to an accession. Call this before download_file.

    See get_filing for what has_nonpublic_counterpart means.
    """
    try:
        filing = await get_client().list_files(accession_number)
    except Exception as exc:
        _tool_error(exc)
        raise
    return {
        "accession_number": filing.accession_number,
        "availability": filing.availability,
        "url": filing.url,
        "has_nonpublic_counterpart": filing.has_nonpublic_counterpart,
        "nonpublic_counterpart_basis": filing.nonpublic_counterpart_basis,
        "files": [_dump(item) for item in filing.files],
    }


@mcp.tool
async def get_filing_text(
    accession_number: str,
    file_id: str | None = None,
    max_chars: int = config.DEFAULT_EXTRACT_CHARS,
) -> dict[str, Any]:
    """Deprecated alias for bounded read_document.

    Returns at most max_chars of extracted text and reports total_chars when
    truncated. Prefer get_document_outline, search_within_document, and
    read_document for large filings.
    """
    try:
        result = await get_client().get_filing_text(
            accession_number,
            file_id=file_id,
            max_chars=max_chars,
        )
    except Exception as exc:
        _tool_error(exc)
        raise
    payload = _dump(result)
    payload["deprecated"] = True
    payload["use_instead"] = "read_document"
    return payload


@mcp.tool
async def read_document(
    accession_number: str,
    filename: str,
    pages: list[int] | None = None,
    char_start: int | None = None,
    char_end: int | None = None,
    max_chars: int = config.MAX_READ_CHARS,
) -> dict[str, Any]:
    """Return bounded plain text from a cached filing attachment.

    Never returns the full document unless it fits within max_chars. Responses
    include total_chars, truncated, and next_char_start / next_page when clipped.
    """
    try:
        return await get_client().read_document(
            accession_number,
            filename,
            pages=pages,
            char_start=char_start,
            char_end=char_end,
            max_chars=max_chars,
        )
    except Exception as exc:
        _tool_error(exc)
        raise


@mcp.tool
async def search_within_document(
    accession_number: str,
    filename: str,
    query: str,
    max_hits: int = 10,
) -> dict[str, Any]:
    """Search extracted text for a query and return passages with page/char offsets."""
    try:
        return await get_client().search_within_document(
            accession_number,
            filename,
            query,
            max_hits=max_hits,
        )
    except Exception as exc:
        _tool_error(exc)
        raise


@mcp.tool
async def get_document_outline(
    accession_number: str,
    filename: str,
) -> dict[str, Any]:
    """Return PDF bookmarks or a heuristic section map for a stored filing."""
    try:
        return await get_client().get_document_outline(accession_number, filename)
    except Exception as exc:
        _tool_error(exc)
        raise


@mcp.tool
async def sync_docket(docket_number: str) -> dict[str, Any]:
    """Incrementally fetch accessions missing from the document store for a docket."""
    try:
        return await get_client().sync_docket(docket_number)
    except Exception as exc:
        _tool_error(exc)
        raise


@mcp.tool
async def cache_status(
    docket: str | None = None,
    accession: str | None = None,
) -> dict[str, Any]:
    """Report what the document store holds for a docket or accession."""
    try:
        return get_client().cache_status(docket=docket, accession=accession)
    except Exception as exc:
        _tool_error(exc)
        raise


@mcp.tool
async def download_file(
    accession_number: str,
    file_id: str | None = None,
    format: DownloadFormat = "native",
) -> dict[str, Any]:
    """Download a public eLibrary file to FERC_DOWNLOAD_DIR.

    Does not return file bytes. Privileged, protected, and CEII documents are
    refused. Call list_files first to pick a file_id.

    format=native saves that one original file and is the default. format=zip
    asks eLibrary for every file on the accession as one archive; if the
    accession has a single attachment, the archive is unwrapped to that file
    and content_type / is_bundle / expected_size describe the saved document.
    format=pdf asks eLibrary to generate a combined PDF of the whole accession.

    The result reports expected_size from FERC's metadata alongside the byte
    count actually written, plus size_matches_metadata and is_bundle, so a
    mismatch between the file you asked for and the artifact you got is visible.
    """
    try:
        result = await get_client().download_file(
            accession_number, file_id=file_id, format=format
        )
    except Exception as exc:
        _tool_error(exc)
        raise
    return _dump(result)


@mcp.tool
async def download_bundle(
    accession_numbers: list[str] | None = None,
    file_ids: list[str] | None = None,
    docket: str | None = None,
    organize_by_accession: bool = True,
) -> dict[str, Any]:
    """Zip many public files into one archive under FERC_DOWNLOAD_DIR/bundles.

    Prefer this over repeated download_file calls. eLibrary's Zip & Download
    accepts many file IDs in a single request (including across accessions), so
    one call replaces N metadata lookups + N downloads + N rate-limit waits.

    Provide any combination of accession_numbers (all public files on each),
    file_ids (exact attachments), and/or docket (public files found via search
    on that docket). Default organize_by_accession=true rewrites FERC's flat
    ``accession_filename`` members into ``accession/filename`` folders.

    Caps: 100 files and 500 MB by default (FERC_MAX_BUNDLE_FILES /
    FERC_MAX_BUNDLE_BYTES). Privileged, protected, and CEII accessions — and
    accessions absent from public search — are skipped and listed in
    skipped_accessions with a reason and category (restricted vs not_found).
    Does not return file bytes.
    """
    try:
        result = await get_client().download_bundle(
            accession_numbers=accession_numbers,
            file_ids=file_ids,
            docket=docket,
            organize_by_accession=organize_by_accession,
        )
    except Exception as exc:
        _tool_error(exc)
        raise
    return _dump(result)


@mcp.tool
async def collect_related(
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
) -> dict[str, Any]:
    """Search a term or document type, then list related filings by docket.

    Caps results at 10 dockets and 50 filings per docket, and reports
    dockets_capped plus filings_capped_per_docket so truncation is visible. If
    download is true, Zip & Downloads up to 10 public files from the search hits
    into one folderized archive (see download_bundle) rather than fetching each
    file individually.

    match, search_in, date_field, and the date-defaulting rules behave as in
    search_filings: supplying docket skips the 60-day default, and the response
    reports date_range_applied, date_range_source, and
    results_may_be_date_limited. Narrow the query before turning downloads on.
    """
    try:
        collection = await get_client().collect_related(
            query=query,
            document_type=document_type,
            docket=docket,
            category=category,
            industry=industry,
            start_date=start_date,
            end_date=end_date,
            download=download,
            match=match,
            search_in=search_in,
            date_field=date_field,
        )
    except Exception as exc:
        _tool_error(exc)
        raise
    return _dump(collection)


class IdleShutdownMiddleware(Middleware):
    """Record when the client last said anything."""

    def __init__(self) -> None:
        self.last_activity = time.monotonic()

    async def on_message(self, context: Any, call_next: Any) -> Any:
        self.last_activity = time.monotonic()
        return await call_next(context)


def _start_idle_watchdog(tracker: IdleShutdownMiddleware, timeout: float) -> threading.Event:
    """Exit if the client stops talking to this instance entirely.

    An abandoned instance never sees EOF because the client keeps its stdin
    open, so it cannot notice on its own that nobody is listening. Shutdown
    goes through SIGTERM, which is the same path a clean stop already takes.
    Returns a stop event so tests can disable the watchdog thread.
    """
    stop = threading.Event()

    def watch() -> None:
        interval = max(1.0, min(60.0, timeout / 4))
        while not stop.wait(interval):
            if time.monotonic() - tracker.last_activity >= timeout:
                signal.raise_signal(signal.SIGTERM)
                return

    threading.Thread(target=watch, name="ferc-idle-watchdog", daemon=True).start()
    return stop


def main() -> None:
    import os

    if config.IDLE_SHUTDOWN_SECONDS > 0:
        tracker = IdleShutdownMiddleware()
        mcp.add_middleware(tracker)
        _start_idle_watchdog(tracker, config.IDLE_SHUTDOWN_SECONDS)
    transport = os.environ.get("FERC_MCP_TRANSPORT", "stdio").strip().lower() or "stdio"
    mcp.run(transport=transport, show_banner=False)


if __name__ == "__main__":
    main()
