from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from ferc_elibrary_mcp.client import (
    ELibraryClient,
    build_search_text,
    guess_content_type,
    split_docket_number,
)
from ferc_elibrary_mcp.config import BASE_URL
from ferc_elibrary_mcp.exceptions import (
    ELibraryRequestError,
    FilingNotFoundError,
    RestrictedDocumentError,
)
from tests.fixtures import SAMPLE_DOCKET_SHEET, SAMPLE_SEARCH, search_with_dockets

SEARCH_URL = re.compile(rf"{re.escape(BASE_URL.rstrip('/'))}/Search/AdvancedSearch")
DOCKET_URL = re.compile(rf"{re.escape(BASE_URL.rstrip('/'))}/Docket/GetSingleDocketSheet")
DOWNLOAD_URL = re.compile(rf"{re.escape(BASE_URL.rstrip('/'))}/File/DownloadP8File")


@pytest.fixture
def client(tmp_path):
    return ELibraryClient(download_dir=tmp_path, rate_limit_seconds=0)


async def test_search_normalizes_accession_and_truncates(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    parsed, hits = await client.search(query="ashokan", docket="P-15056-000")
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
    parsed, _ = await client.search(query="pipeline")
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
    )
    with pytest.raises(FilingNotFoundError):
        await client.get_filing("20201119-9999")


async def test_get_docket_sheet(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=DOCKET_URL, json=SAMPLE_DOCKET_SHEET)
    sheet = await client.get_docket("P-15056-000")
    assert sheet.applicants == ["Premium Energy Holdings, LLC"]
    assert sheet.filings[0].accession_number == "20201119-5202"
    assert "elibrary.ferc.gov" in sheet.filings[0].url


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
