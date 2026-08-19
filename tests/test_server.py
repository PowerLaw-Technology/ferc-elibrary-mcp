from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pytest_httpx import HTTPXMock

from ferc_elibrary_mcp.client import ELibraryClient
from ferc_elibrary_mcp.server import mcp, set_client
from tests.fixtures import (
    SAMPLE_DOCKET_SHEET,
    SAMPLE_SEARCH,
    search_with_dockets,
    search_with_files,
)
from tests.test_client import DOCKET_URL, SEARCH_URL

EMPTY_SEARCH = {
    "searchHits": [],
    "totalHits": 0,
    "numHits": 0,
    "success": True,
    "errorMessage": None,
}


@pytest.fixture
def elibrary(tmp_path):
    client = ELibraryClient(download_dir=tmp_path, rate_limit_seconds=0)
    set_client(client)
    yield client
    set_client(None)


async def test_tools_are_registered(elibrary):
    async with Client(mcp) as session:
        tools = await session.list_tools()
        names = {tool.name for tool in tools}
        assert names == {
            "search_filings",
            "get_docket",
            "get_filing",
            "list_files",
            "download_file",
            "collect_related",
        }


async def test_search_filings_tool(httpx_mock: HTTPXMock, elibrary):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    async with Client(mcp) as session:
        result = await session.call_tool(
            "search_filings",
            {"query": "ashokan", "docket": "P-15056-000"},
        )
    data = result.data
    assert data["total_hits"] == 1
    assert data["hits"][0]["accession_number"] == "20201119-5202"


async def test_search_filings_tool_reports_date_envelope(
    httpx_mock: HTTPXMock, elibrary
):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    async with Client(mcp) as session:
        result = await session.call_tool("search_filings", {"docket": "EL25-49"})
    data = result.data
    assert data["date_range_source"] == "none"
    assert data["results_may_be_date_limited"] is False
    assert data["date_range_applied"] == {"start": None, "end": None}
    assert data["date_field_applied"] == "filed"


async def test_search_filings_tool_flags_default_window(
    httpx_mock: HTTPXMock, elibrary
):
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    async with Client(mcp) as session:
        result = await session.call_tool(
            "search_filings", {"query": "interconnection"}
        )
    data = result.data
    assert data["date_range_source"] == "default_60_day"
    assert data["results_may_be_date_limited"] is True
    assert data["date_range_applied"]["start"] is not None


async def test_empty_result_still_reports_date_range(httpx_mock: HTTPXMock, elibrary):
    """An empty set under an unseen default is the case that misleads."""
    httpx_mock.add_response(url=SEARCH_URL, json=EMPTY_SEARCH)
    async with Client(mcp) as session:
        result = await session.call_tool(
            "search_filings", {"query": "zzzznonexistentzzzz"}
        )
    data = result.data
    assert data["total_hits"] == 0
    assert data["date_range_source"] == "default_60_day"
    assert data["results_may_be_date_limited"] is True


async def test_search_filings_tool_rejects_bad_date(httpx_mock: HTTPXMock, elibrary):
    async with Client(mcp) as session:
        with pytest.raises(ToolError, match="YYYY-MM-DD or MM/DD/YYYY"):
            await session.call_tool(
                "search_filings",
                {"docket": "EL25-49", "start_date": "yesterday"},
            )


async def test_collect_related_tool_reports_date_range(
    httpx_mock: HTTPXMock, elibrary
):
    httpx_mock.add_response(url=SEARCH_URL, json=search_with_dockets(["EL25-49"]))
    httpx_mock.add_response(url=DOCKET_URL, json=SAMPLE_DOCKET_SHEET, is_reusable=True)
    async with Client(mcp) as session:
        result = await session.call_tool("collect_related", {"docket": "EL25-49"})
    assert result.data["date_range_source"] == "none"
    assert result.data["date_field_applied"] == "filed"


async def test_list_files_tool_reports_counterpart(httpx_mock: HTTPXMock, elibrary):
    httpx_mock.add_response(
        url=SEARCH_URL,
        json=search_with_files([{"fileName": "PUBLIC_NextEra_Exhibit_A.pdf"}]),
    )
    async with Client(mcp) as session:
        result = await session.call_tool(
            "list_files", {"accession_number": "20260710-5118"}
        )
    assert result.data["has_nonpublic_counterpart"] is True
    assert result.data["nonpublic_counterpart_basis"] == "file_naming_convention"


async def test_get_docket_tool(httpx_mock: HTTPXMock, elibrary):
    httpx_mock.add_response(url=DOCKET_URL, json=SAMPLE_DOCKET_SHEET)
    async with Client(mcp) as session:
        result = await session.call_tool("get_docket", {"docket_number": "P-15056-000"})
    assert result.data["filings"][0]["accession_number"] == "20201119-5202"


async def test_download_file_tool_rejects_privileged(httpx_mock: HTTPXMock, elibrary):
    httpx_mock.add_response(
        url=SEARCH_URL, json=search_with_dockets(["CP21-470"], avail="N")
    )
    async with Client(mcp) as session:
        with pytest.raises(ToolError, match="Privileged"):
            await session.call_tool("download_file", {"accession_number": "20201119-5202"})


async def test_collect_related_tool_caps(httpx_mock: HTTPXMock, elibrary):
    dockets = [f"ER24-{i}" for i in range(12)]
    httpx_mock.add_response(url=SEARCH_URL, json=search_with_dockets(dockets))
    httpx_mock.add_response(url=DOCKET_URL, json=SAMPLE_DOCKET_SHEET, is_reusable=True)
    async with Client(mcp) as session:
        result = await session.call_tool(
            "collect_related", {"query": "capacity", "document_type": "Order/Opinion"}
        )
    assert result.data["dockets_capped"] is True
    assert result.data["dockets_returned"] == 10
