from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from ferc_elibrary_mcp.client import ELibraryClient
from ferc_elibrary_mcp.ferc.rate_limit import TokenBucketLimiter
from tests.fixtures import SAMPLE_DOCKET_SHEET, SAMPLE_SEARCH
from tests.test_client import DOCKET_URL, DOWNLOAD_URL, SEARCH_URL
from tests.test_textextract import _hand_rolled_pdf


@pytest.fixture
def client(tmp_path):
    return ELibraryClient(download_dir=tmp_path, rate_limit_seconds=0)


async def test_download_file_cache_hit_no_ferc_call(httpx_mock: HTTPXMock, client, tmp_path):
    pdf = _hand_rolled_pdf("cached once")
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    httpx_mock.add_response(url=DOWNLOAD_URL, content=pdf)
    first = await client.download_file("20201119-5202")
    assert first.skipped is False
    second = await client.download_file("20201119-5202")
    assert second.path == first.path
    assert len(httpx_mock.get_requests()) == 2


SAMPLE_FILENAME = "Premium Energy Preliminary Permit App Ashokan PSP.PDF"


async def test_read_document_reports_total_and_truncation(
    httpx_mock: HTTPXMock, client
):
    pdf = _hand_rolled_pdf("A" * 500)
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    httpx_mock.add_response(url=DOWNLOAD_URL, content=pdf)
    read = await client.read_document(
        "20201119-5202", SAMPLE_FILENAME, max_chars=50
    )
    assert read["total_chars"] > 50
    assert read["truncated"] is True
    assert read["next_char_start"] is not None


async def test_sync_docket_incremental(httpx_mock: HTTPXMock, client, tmp_path):
    sheet = {
        **SAMPLE_DOCKET_SHEET,
        "DataList": [
            {
                "DocumentsItem": [
                    {
                        "accession_no": "20201119-5202",
                        "doc_desc": "Cached filing",
                        "filed_date": "2026-01-01T00:00:00",
                        "issued_date": "0001-01-01T00:00:00",
                        "DOCKET_TEXT": "P-15056",
                        "SUBDOCKET_TEXT": "000",
                        "category": "Submittal",
                        "Affiliation_Organization": [],
                    },
                    {
                        "accession_no": "20201119-5203",
                        "doc_desc": "New filing",
                        "filed_date": "2026-01-02T00:00:00",
                        "issued_date": "0001-01-01T00:00:00",
                        "DOCKET_TEXT": "P-15056",
                        "SUBDOCKET_TEXT": "000",
                        "category": "Submittal",
                        "Affiliation_Organization": [],
                    },
                ]
            }
        ],
    }
    pdf = _hand_rolled_pdf("new filing body")
    httpx_mock.add_response(url=DOCKET_URL, json=sheet)
    client.store.put("P-15056-000", "20201119-5202", "cached.pdf", pdf)
    result = await client.sync_docket("P-15056-000")
    assert "20201119-5202" in result["already_cached"]


async def test_token_bucket_limits_rps() -> None:
    limiter = TokenBucketLimiter(10.0, burst=1)
    import time

    start = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.08
