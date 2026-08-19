from __future__ import annotations

import re

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from ferc_elibrary_mcp.client import ELibraryClient
from ferc_elibrary_mcp.config import BASE_URL
from ferc_elibrary_mcp.exceptions import FilingNotFoundError, RestrictedDocumentError
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


async def test_download_public_native_file(httpx_mock: HTTPXMock, client):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    httpx_mock.add_response(
        url=DOWNLOAD_URL,
        content=b"%PDF-1.4 fake",
        headers={"content-type": "application/pdf"},
    )
    result = await client.download_file("20201119-5202", format="native")
    assert result.skipped is False
    assert result.size == len(b"%PDF-1.4 fake")
    saved = Path(result.path)
    assert saved.exists()
    assert saved.read_bytes().startswith(b"%PDF")


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
