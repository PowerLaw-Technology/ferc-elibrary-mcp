from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from ferc_elibrary_mcp.client import (
    ELibraryClient,
    accessions_in_zip,
    build_search_text,
    folderize_zip,
    guess_content_type,
    resolve_date_range,
    split_docket_number,
)
from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.config import BASE_URL
from ferc_elibrary_mcp.exceptions import (
    ELibraryRequestError,
    FilingNotFoundError,
    RestrictedDocumentError,
)
from ferc_elibrary_mcp.models import FileSummary, detect_nonpublic_counterpart
from tests.fixtures import (
    CROSS_DOCKETED_SHEET,
    EMPTY_SEARCH,
    SAMPLE_DOCKET_SHEET,
    SAMPLE_SEARCH,
    docket_sheet,
    docket_sheet_row,
    search_with_dockets,
    search_with_files,
)

SEARCH_URL = re.compile(rf"{re.escape(BASE_URL.rstrip('/'))}/Search/AdvancedSearch")
DOCKET_URL = re.compile(rf"{re.escape(BASE_URL.rstrip('/'))}/Docket/GetSingleDocketSheet")
DOWNLOAD_URL = re.compile(rf"{re.escape(BASE_URL.rstrip('/'))}/File/DownloadP8File")


@pytest.fixture
def client(tmp_path):
    return ELibraryClient(download_dir=tmp_path, rate_limit_seconds=0)


async def test_search_normalizes_accession_and_truncates(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    parsed, hits, _ = await client.search(query="ashokan", docket="P-15056-000")
    assert parsed.total_hits == 1
    assert hits[0].accession_number == "20201119-5202"
    assert "acesssion" not in hits[0].model_dump()
    assert hits[0].availability == "Public"
    assert hits[0].files[0].file_id == "020AAB97-66E2-5005-8110-C31FAFC91712"
    body = httpx_mock.get_requests()[0].read()
    assert b'"availability": ["P"]' in body or b'"availability":["P"]' in body


def test_split_docket_number():
    assert split_docket_number("ER26-3178-000") == ("ER26-3178", "000")
    assert split_docket_number("P-15056-000") == ("P-15056", "000")
    assert split_docket_number("CP21-470") == ("CP21-470", None)
    assert split_docket_number("ER26") == ("ER26", None)


async def test_search_without_docket_sends_empty_filter(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    await client.search(query="pipeline")
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["docketSearches"] == []
    assert "accessionNumber" not in payload


async def test_search_by_accession_omits_null_docket(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    await client.get_filing("20201119-5202")
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["docketSearches"] == []
    assert payload["accessionNumber"] == "20201119-5202"
    assert payload["allDates"] is True


async def test_search_surfaces_ferc_null_reference(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(
        url=SEARCH_URL,
        json={
            "searchHits": None,
            "totalHits": 0,
            "numHits": 0,
            "success": False,
            "errorMessage": "Object reference not set to an instance of an object.",
        },
    )
    with pytest.raises(ELibraryRequestError, match="Object reference"):
        await client.search(query="pipeline")


async def test_transient_proxy_error_is_retried(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, status_code=520, text="upstream boom")
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    parsed, _, _dates = await client.search(query="pipeline")
    assert parsed.total_hits == 1
    assert len(httpx_mock.get_requests()) == 2


async def test_persistent_proxy_error_gives_up(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, status_code=503, is_reusable=True)
    with pytest.raises(ELibraryRequestError, match="after 3 attempts"):
        await client.search(query="pipeline")


async def test_get_docket_strips_subdocket_suffix(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=DOCKET_URL, json=SAMPLE_DOCKET_SHEET)
    sheet = await client.get_docket("P-15056-000")
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["dockets"] == "P-15056"
    assert payload["subdockets"] == "All"
    assert sheet.docket_number == "P-15056"


async def test_search_rejects_non_json(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, text="<html>nope</html>")
    with pytest.raises(Exception, match="non-JSON"):
        await client.search(query="pipeline")


async def test_get_filing_not_found(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(
        url=SEARCH_URL,
        json={
            "searchHits": [],
            "totalHits": 0,
            "numHits": 0,
            "success": True,
            "errorMessage": None,
        },
        is_reusable=True,
    )
    with pytest.raises(FilingNotFoundError, match="not in public eLibrary search"):
        await client.get_filing("20201119-9999")
    bodies = [json.loads(req.content) for req in httpx_mock.get_requests()]
    assert bodies[0]["availability"] == ["P"]
    assert bodies[1]["availability"] == []


async def test_get_docket_sheet(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=DOCKET_URL, json=SAMPLE_DOCKET_SHEET)
    sheet = await client.get_docket("P-15056-000")
    assert sheet.applicants == ["Premium Energy Holdings, LLC"]
    assert sheet.filings[0].accession_number == "20201119-5202"
    assert "elibrary.ferc.gov" in sheet.filings[0].url


def test_docket_scope_suppresses_date_default():
    """A named docket is an intentional scope; a 60-day window would hide it."""
    resolved = resolve_date_range(None, None, docket="EL25-49")
    assert resolved.source == "none"
    assert resolved.start is None and resolved.end is None
    assert resolved.may_be_date_limited is False


def test_accession_scope_suppresses_date_default():
    resolved = resolve_date_range(None, None, accession_number="20250220-3091")
    assert resolved.source == "none"


def test_open_ended_query_keeps_default_window():
    resolved = resolve_date_range(None, None)
    assert resolved.source == "default_60_day"
    assert resolved.may_be_date_limited is True
    expected = (date.today() - timedelta(days=60)).isoformat()
    assert resolved.start == expected


def test_explicit_dates_win_over_scope():
    resolved = resolve_date_range("2024-01-01", "2026-08-19", docket="EL25-49")
    assert resolved.source == "explicit"
    assert resolved.start == "2024-01-01"
    assert resolved.end == "2026-08-19"
    assert resolved.may_be_date_limited is False


def test_one_sided_explicit_range_is_explicit():
    assert resolve_date_range("2024-01-01", None).source == "explicit"
    assert resolve_date_range(None, "2024-01-01").source == "explicit"


def test_date_field_must_be_known():
    # eLibrary returns zero hits for an unknown dateType instead of erroring.
    with pytest.raises(ValueError, match="Unrecognized date_field"):
        resolve_date_range(None, None, date_field="issuedDate")


def test_date_envelope_shape():
    envelope = resolve_date_range(None, None, docket="EL25-49").as_envelope()
    assert envelope == {
        "date_range_applied": {"start": None, "end": None},
        "date_range_source": "none",
        "date_field_applied": "filed",
        "results_may_be_date_limited": False,
        "date_field_filtered_client_side": False,
    }


async def test_docket_search_omits_date_filter(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    _parsed, _hits, dates = await client.search(docket="EL25-49")
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["allDates"] is True
    assert payload["dateSearches"] == []
    assert dates.source == "none"


async def test_bare_query_sends_date_filter(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    _parsed, _hits, dates = await client.search(query="interconnection")
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["allDates"] is False
    assert payload["dateSearches"][0]["dateType"] == "filed_date"
    assert dates.source == "default_60_day"


async def test_issued_date_field_changes_wire_date_type(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    _parsed, _hits, dates = await client.search(
        docket="ER26-3176",
        start_date="2026-08-06",
        end_date="2026-08-06",
        date_field="issued",
    )
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["dateSearches"][0]["dateType"] == "issued_date"
    assert dates.field == "issued"
    assert dates.as_envelope()["date_field_applied"] == "issued"


async def test_filed_date_is_the_default_field(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    await client.search(docket="ER26-3176", start_date="2026-08-06")
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["dateSearches"][0]["dateType"] == "filed_date"


async def test_collect_related_reports_date_range(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=search_with_dockets(["ER26-1"]))
    httpx_mock.add_response(url=DOCKET_URL, json=SAMPLE_DOCKET_SHEET)
    collection = await client.collect_related(query="interconnection")
    assert collection.date_range_source == "default_60_day"
    assert collection.results_may_be_date_limited is True
    assert collection.date_range_applied["start"] is not None
    assert collection.date_field_applied == "filed"


async def test_collect_related_docket_scope_has_no_date_default(
    httpx_mock: HTTPXMock, client
):
    httpx_mock.add_response(url=SEARCH_URL, json=search_with_dockets(["EL25-49"]))
    httpx_mock.add_response(url=DOCKET_URL, json=SAMPLE_DOCKET_SHEET)
    collection = await client.collect_related(docket="EL25-49")
    assert collection.date_range_source == "none"
    assert collection.results_may_be_date_limited is False


def test_malformed_date_message_names_both_formats():
    with pytest.raises(ValueError, match="YYYY-MM-DD or MM/DD/YYYY"):
        resolve_date_range("yesterday", None)


@pytest.mark.parametrize(
    "files,expected",
    [
        ([{"fileDesc": "PUBLIC Equitrans, L.P. Answer to Complaint"}], True),
        ([{"fileName": "PUBLIC_NextEra_Exhibit_A__(FINAL).pdf"}], True),
        ([{"fileName": "Redacted Exhibit B.pdf"}], True),
        ([{"fileDesc": "Public Version of Testimony"}], True),
        ([{"fileName": "Sebree Solar SFA.pdf"}], False),
        # Utility names starting with "Public" must not trip the heuristic.
        ([{"fileDesc": "Public Service Company of Colorado tariff filing"}], False),
        ([{"fileName": "Public Utility Regulatory Policies Act filing.pdf"}], False),
        ([{"fileDesc": "Comments in the public interest"}], False),
    ],
)
def test_detect_nonpublic_counterpart(files, expected):
    summaries = [
        FileSummary(
            file_id=f"F{i}",
            file_name=f.get("fileName", ""),
            file_type="PDF",
            file_size=100,
            description=f.get("fileDesc", ""),
        )
        for i, f in enumerate(files)
    ]
    flagged, basis = detect_nonpublic_counterpart(summaries)
    assert flagged is expected
    assert basis == ("file_naming_convention" if expected else None)


async def test_get_filing_flags_nonpublic_counterpart(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(
        url=SEARCH_URL,
        json=search_with_files(
            [{"fileDesc": "PUBLIC Equitrans, L.P. Answer to Complaint of M4 Energy"}]
        ),
    )
    filing = await client.get_filing("20260817-5147")
    assert filing.has_nonpublic_counterpart is True
    assert filing.nonpublic_counterpart_basis == "file_naming_convention"


async def test_get_filing_no_counterpart_on_ordinary_filing(
    httpx_mock: HTTPXMock, client
):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    filing = await client.get_filing("20201119-5202")
    assert filing.has_nonpublic_counterpart is False
    assert filing.nonpublic_counterpart_basis is None


async def test_docket_sheet_dedupes_associations(httpx_mock: HTTPXMock, client):
    """FERC counts docket associations; callers want filings."""
    httpx_mock.add_response(url=DOCKET_URL, json=CROSS_DOCKETED_SHEET)
    sheet = await client.get_docket("EL25-49", limit=100)
    assert sheet.total_hits == 2
    assert sheet.count_basis == "distinct_accession"
    assert len(sheet.filings) == 2
    assert len({f.accession_number for f in sheet.filings}) == 2


async def test_docket_sheet_merges_full_docket_array(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=DOCKET_URL, json=CROSS_DOCKETED_SHEET)
    sheet = await client.get_docket("EL25-49", limit=100)
    row = next(f for f in sheet.filings if f.accession_number == "20260219-5002")
    assert row.docket_numbers == ["EL25-49-000", "EL25-49-001", "EL25-49-002"]
    assert row.sub_dockets == ["000", "001", "002"]
    assert sheet.includes_subdockets == ["000", "001", "002"]


async def test_docket_sheet_total_matches_retrievable_rows(
    httpx_mock: HTTPXMock, client
):
    httpx_mock.add_response(url=DOCKET_URL, json=CROSS_DOCKETED_SHEET, is_reusable=True)
    collected: set[str] = set()
    page = 1
    while page <= 5:
        sheet = await client.get_docket("EL25-49", limit=1, page=page)
        if not sheet.filings:
            break
        collected |= {f.accession_number for f in sheet.filings}
        page += 1
    first = await client.get_docket("EL25-49", limit=1, page=1)
    assert first.total_hits == len(collected)


async def test_docket_sheet_ignores_ferc_association_count(
    httpx_mock: HTTPXMock, client
):
    httpx_mock.add_response(
        url=DOCKET_URL, json=docket_sheet(CROSS_DOCKETED_SHEET["DataList"][0]
                                          ["DocumentsItem"], reported_total=380)
    )
    sheet = await client.get_docket("EL25-49", limit=100)
    assert sheet.total_hits == 2


async def test_docket_sheet_reports_date_envelope(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=DOCKET_URL, json=CROSS_DOCKETED_SHEET)
    sheet = await client.get_docket("EL25-49")
    assert sheet.date_range_source == "none"
    assert sheet.results_may_be_date_limited is False
    assert sheet.date_range_applied == {"start": None, "end": None}
    assert sheet.date_field_applied == "filed"


async def test_docket_sheet_never_defaults_to_60_days(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=DOCKET_URL, json=CROSS_DOCKETED_SHEET)
    sheet = await client.get_docket("EL25-49")
    # A docket number is always scoped, so the 60-day default can never apply.
    assert sheet.date_range_source != "default_60_day"
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["filed_date_beg"] == "01-01-1960"


async def test_docket_sheet_pagination_is_one_indexed(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=DOCKET_URL, json=CROSS_DOCKETED_SHEET, is_reusable=True)
    page1 = await client.get_docket("EL25-49", limit=1, page=1)
    page0 = await client.get_docket("EL25-49", limit=1, page=0)
    default = await client.get_docket("EL25-49", limit=1)
    page2 = await client.get_docket("EL25-49", limit=1, page=2)
    assert page1.page_base == 1
    first = page1.filings[0].accession_number
    # page=0 is accepted as the first page rather than erroring.
    assert page0.filings[0].accession_number == first
    assert default.filings[0].accession_number == first
    assert page2.filings[0].accession_number != first


async def test_docket_sheet_sort_order(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=DOCKET_URL, json=CROSS_DOCKETED_SHEET, is_reusable=True)
    oldest = await client.get_docket("EL25-49", limit=100)
    newest = await client.get_docket("EL25-49", limit=100, sort_order="newest_first")
    assert oldest.sort_order == "oldest_first"
    assert oldest.filings[0].accession_number == "20250220-3091"
    assert newest.filings[0].accession_number == "20260219-5002"
    assert oldest.total_hits == newest.total_hits


async def test_docket_sheet_suppresses_null_issued_sentinel(
    httpx_mock: HTTPXMock, client
):
    httpx_mock.add_response(url=DOCKET_URL, json=CROSS_DOCKETED_SHEET)
    sheet = await client.get_docket("EL25-49", limit=100)
    # 0001-01-01 is the sheet's null, not a date in year 1.
    assert all(f.issued_date == "" for f in sheet.filings)


async def test_docket_sheet_normalizes_dates_like_search(
    httpx_mock: HTTPXMock, client
):
    httpx_mock.add_response(url=DOCKET_URL, json=CROSS_DOCKETED_SHEET)
    sheet = await client.get_docket("EL25-49", limit=100)
    assert {f.filed_date for f in sheet.filings} == {"02/19/2026", "02/20/2025"}


async def test_docket_issued_window_resolves_through_search(
    httpx_mock: HTTPXMock, client
):
    """The sheet reports every issued_date as null, so search answers instead."""
    httpx_mock.add_response(
        url=SEARCH_URL, json=search_with_dockets(["EL25-49"])  # 20201119-5202
    )
    httpx_mock.add_response(url=SEARCH_URL, json=EMPTY_SEARCH)
    httpx_mock.add_response(
        url=DOCKET_URL,
        json=docket_sheet(
            [
                docket_sheet_row("20201119-5202", sub="000"),
                docket_sheet_row("20260219-5002", sub="000"),
            ]
        ),
    )
    sheet = await client.get_docket(
        "EL25-49", start_date="2026-08-06", end_date="2026-08-06", date_field="issued"
    )
    assert sheet.date_field_applied == "issued"
    assert sheet.date_field_filtered_client_side is True
    assert {f.accession_number for f in sheet.filings} == {"20201119-5202"}
    # The issuance window must not also be sent as a filed-date window.
    docket_payload = json.loads(httpx_mock.get_requests()[-1].content)
    assert docket_payload["filed_date_beg"] == "01-01-1960"


async def test_docket_filed_window_stays_server_side(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=DOCKET_URL, json=CROSS_DOCKETED_SHEET)
    sheet = await client.get_docket(
        "EL25-49", start_date="2025-01-01", end_date="2025-12-31"
    )
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["filed_date_beg"] == "01-01-2025"
    assert payload["filed_date_end"] == "12-31-2025"
    assert sheet.date_field_filtered_client_side is False
    assert sheet.date_range_source == "explicit"


async def test_docket_sheet_requests_everything_once(httpx_mock: HTTPXMock, client):
    """numHits/pageNumber do not slice reliably, so paging happens locally."""
    httpx_mock.add_response(url=DOCKET_URL, json=CROSS_DOCKETED_SHEET)
    await client.get_docket("EL25-49", limit=5, page=3)
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["numHits"] == config.DOCKET_SHEET_FETCH_LIMIT
    assert payload["pageNumber"] == 0


def test_build_search_text():
    assert build_search_text("master service agreement", "phrase") == (
        '"master service agreement"'
    )
    assert build_search_text("master service agreement", "all") == (
        "master AND service AND agreement"
    )
    assert build_search_text("master service agreement", "any") == (
        "master service agreement"
    )
    assert build_search_text("pipeline", "phrase") == "pipeline"
    assert build_search_text(None, "phrase") == "*"
    # Caller-supplied syntax passes through untouched.
    assert build_search_text('"already quoted"', "phrase") == '"already quoted"'
    assert build_search_text("a AND b", "phrase") == "a AND b"


def test_guess_content_type_ignores_octet_stream():
    assert guess_content_type(b"%PDF-1.7 ...", "thing.pdf") == "application/pdf"
    assert guess_content_type(b"PK\x03\x04junk", "bundle.pdf") == "application/zip"
    assert guess_content_type(b"", "report.docx").endswith("wordprocessingml.document")
    assert guess_content_type(b"", "scan.TIF") == "image/tiff"
    assert guess_content_type(b"\x00\x01", "mystery.bin") == "application/octet-stream"


async def test_search_phrase_mode_quotes_multiword(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    await client.search(query="shared facilities agreement")
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["searchText"] == '"shared facilities agreement"'
    assert payload["searchFullText"] is True
    assert payload["searchDescription"] is True


async def test_search_in_description_only(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    await client.search(query="shared facilities", search_in="description")
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["searchDescription"] is True
    assert payload["searchFullText"] is False


async def test_download_public_native_file(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    httpx_mock.add_response(
        url=DOWNLOAD_URL,
        content=b"%PDF-1.4 fake",
        headers={"content-type": "application/octet-stream"},
    )
    result = await client.download_file("20201119-5202", format="native")
    assert result.skipped is False
    assert result.size == len(b"%PDF-1.4 fake")
    assert result.content_type == "application/pdf"
    assert result.is_bundle is False
    saved = Path(result.path)
    assert saved.exists()
    assert saved.read_bytes().startswith(b"%PDF")


async def test_native_download_requests_single_file(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    httpx_mock.add_response(url=DOWNLOAD_URL, content=b"%PDF-1.4 fake")
    await client.download_file("20201119-5202", format="native")
    body = json.loads(httpx_mock.get_requests()[-1].content)
    # A populated "accession" makes FERC return a zip of the whole accession.
    assert body["accession"] == ""
    assert body["fileidLst"] == ["020AAB97-66E2-5005-8110-C31FAFC91712"]


async def test_download_flags_size_mismatch_and_bundle(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    httpx_mock.add_response(
        url=DOWNLOAD_URL,
        content=b"PK\x03\x04" + b"x" * 100,
        headers={"content-disposition": "attachment; filename=20201119-5202_1343.zip"},
    )
    result = await client.download_file("20201119-5202", format="native")
    assert result.content_type == "application/zip"
    assert result.is_bundle is True
    assert result.expected_size == 472488
    assert result.size_matches_metadata is False
    assert Path(result.path).name == "20201119-5202_1343.zip"


async def test_zip_format_requests_all_files(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    httpx_mock.add_response(url=DOWNLOAD_URL, content=b"PK\x03\x04zip")
    result = await client.download_file("20201119-5202", format="zip")
    body = json.loads(httpx_mock.get_requests()[-1].content)
    assert body["accession"] == "20201119-5202"
    assert result.is_bundle is True


def test_folderize_zip_builds_accession_folders():
    import io
    import zipfile

    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as zf:
        zf.writestr("20260716-5098_Agreement.pdf", b"%PDF-a")
        zf.writestr("20260817-5147_Answer.pdf", b"%PDF-b")
        zf.writestr("readme.txt", b"hi")
    out = folderize_zip(raw.getvalue())
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        names = set(zf.namelist())
    assert names == {
        "20260716-5098/Agreement.pdf",
        "20260817-5147/Answer.pdf",
        "readme.txt",
    }
    assert accessions_in_zip(out) == ["20260716-5098", "20260817-5147"]


async def test_download_bundle_one_request_many_ids(httpx_mock: HTTPXMock, client):
    import io
    import zipfile

    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as zf:
        zf.writestr("20201119-5202_App.PDF", b"%PDF-1")
        zf.writestr("20260716-5098_SFA.pdf", b"%PDF-2")
    httpx_mock.add_response(
        url=DOWNLOAD_URL,
        content=packed.getvalue(),
        headers={"content-disposition": "attachment; filename=bundle.zip"},
    )
    result = await client.download_bundle(
        file_ids=[
            "020AAB97-66E2-5005-8110-C31FAFC91712",
            "77602C02-8449-CD32-8585-9F6C2BE00000",
        ]
    )
    assert result.skipped is False
    assert result.file_count == 2
    assert result.organized_by_accession is True
    assert Path(result.path).exists()
    body = json.loads(httpx_mock.get_requests()[-1].content)
    assert body["accession"] == ""
    assert len(body["fileidLst"]) == 2
    with zipfile.ZipFile(result.path) as zf:
        assert set(zf.namelist()) == {
            "20201119-5202/App.PDF",
            "20260716-5098/SFA.pdf",
        }


async def test_download_bundle_skips_restricted_accession(
    httpx_mock: HTTPXMock, client
):
    httpx_mock.add_response(
        url=SEARCH_URL, json=search_with_dockets(["CP21-470"], avail="C")
    )
    result = await client.download_bundle(accession_numbers=["20201119-5202"])
    assert result.skipped is True
    assert result.skipped_accessions == ["20201119-5202"]
    assert not any("DownloadP8File" in str(r.url) for r in httpx_mock.get_requests())


async def test_get_filing_retries_without_public_filter(
    httpx_mock: HTTPXMock, client
):
    """Privileged filings are omitted from public search, not nonexistent."""
    httpx_mock.add_response(url=SEARCH_URL, json=EMPTY_SEARCH)
    httpx_mock.add_response(
        url=SEARCH_URL, json=search_with_dockets(["CP21-470"], avail="N")
    )
    filing = await client.get_filing("20201119-5202")
    assert filing.availability == "Privileged"
    bodies = [json.loads(req.content) for req in httpx_mock.get_requests()]
    assert bodies[0]["availability"] == ["P"]
    assert bodies[1]["availability"] == []


async def test_download_bundle_skips_accession_absent_from_public_search(
    httpx_mock: HTTPXMock, client
):
    """A privileged miss must not abort the rest of the bundle with 'not found'."""
    import io
    import zipfile

    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as zf:
        zf.writestr("20201119-5202_App.PDF", b"%PDF-1")
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    httpx_mock.add_response(url=SEARCH_URL, json=EMPTY_SEARCH, is_reusable=True)
    httpx_mock.add_response(
        url=DOWNLOAD_URL,
        content=packed.getvalue(),
        headers={"content-disposition": "attachment; filename=bundle.zip"},
    )
    result = await client.download_bundle(
        accession_numbers=["20201119-5202", "20201119-9999"]
    )
    assert result.skipped is False
    assert result.skipped_accessions == ["20201119-9999"]
    assert Path(result.path).exists()
    assert result.file_count == 1


async def test_collect_related_download_uses_bundle(httpx_mock: HTTPXMock, client):
    import io
    import zipfile

    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as zf:
        zf.writestr("20201119-5202_App.PDF", b"%PDF")
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    httpx_mock.add_response(url=DOCKET_URL, json=SAMPLE_DOCKET_SHEET, is_reusable=True)
    httpx_mock.add_response(url=DOWNLOAD_URL, content=packed.getvalue())
    collection = await client.collect_related(query="ashokan", download=True)
    assert collection.bundle is not None
    assert collection.bundle.file_count == 1
    assert collection.bundle.path
    assert len(collection.downloads) == 1
    assert collection.downloads[0].is_bundle is True
    download_calls = [
        r for r in httpx_mock.get_requests() if "DownloadP8File" in str(r.url)
    ]
    assert len(download_calls) == 1


async def test_download_rejects_ceii(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=search_with_dockets(["CP21-470"], avail="C"))
    with pytest.raises(RestrictedDocumentError, match="CEII"):
        await client.download_file("20201119-5202")
    urls = [str(req.url) for req in httpx_mock.get_requests()]
    assert not any("DownloadP8File" in url for url in urls)


async def test_download_skips_oversize_file(httpx_mock: HTTPXMock, client):
    payload = search_with_dockets(["P-15056-000"])
    payload["searchHits"][0]["transmittals"][0]["fileSize"] = 50 * 1024 * 1024
    httpx_mock.add_response(url=SEARCH_URL, json=payload)
    result = await client.download_file("20201119-5202", max_bytes=1024)
    assert result.skipped is True
    assert "over the" in (result.skip_reason or "")


async def test_collect_related_respects_docket_cap(httpx_mock: HTTPXMock, client):
    dockets = [f"CP21-{i:03d}" for i in range(12)]
    httpx_mock.add_response(url=SEARCH_URL, json=search_with_dockets(dockets))
    httpx_mock.add_response(url=DOCKET_URL, json=SAMPLE_DOCKET_SHEET, is_reusable=True)
    collection = await client.collect_related(query="pipeline", max_dockets=10)
    assert collection.dockets_capped is True
    assert collection.dockets_returned == 10
    docket_calls = [
        req for req in httpx_mock.get_requests() if "GetSingleDocketSheet" in str(req.url)
    ]
    assert len(docket_calls) == 10
