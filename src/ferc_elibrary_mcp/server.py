from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.client import ELibraryClient
from ferc_elibrary_mcp.exceptions import ELibraryError
from ferc_elibrary_mcp.models import (
    DateField,
    DownloadFormat,
    MatchMode,
    SearchScope,
)

mcp = FastMCP(
    name="ferc-elibrary",
    instructions=(
        "Search the public FERC eLibrary for dockets and filings. "
        "Use search_filings for keywords or document types, get_docket for a docket "
        "sheet of related filings, get_filing/list_files for one accession, "
        "download_file to save a public document locally, and collect_related to "
        "search a term and gather related docket filings. Only public documents "
        "can be downloaded. Dates use YYYY-MM-DD. Prefer pagination over huge dumps."
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
    page: int = 0,
    limit: int = config.DEFAULT_SEARCH_LIMIT,
) -> dict[str, Any]:
    """Return the docket sheet: related filings, applicants, and accession numbers.

    Docket numbers look like CP21-470, ER11-4046, or P-15056-000. Subdockets can
    be All or a comma-separated list such as 000,001.
    """
    try:
        sheet = await get_client().get_docket(
            docket_number,
            subdockets=subdockets,
            start_date=start_date,
            end_date=end_date,
            page=page,
            limit=limit,
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
async def download_file(
    accession_number: str,
    file_id: str | None = None,
    format: DownloadFormat = "native",
) -> dict[str, Any]:
    """Download a public eLibrary file to FERC_DOWNLOAD_DIR.

    Does not return file bytes. Privileged, protected, and CEII documents are
    refused. Call list_files first to pick a file_id.

    format=native saves that one original file and is the default. format=zip
    bundles every file on the accession into one archive. format=pdf asks
    eLibrary to generate a combined PDF of the whole accession.

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
    download is true, saves up to 10 public files under 25 MB each.

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


def main() -> None:
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
