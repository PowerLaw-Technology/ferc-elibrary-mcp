from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pytest_httpx import HTTPXMock

from ferc_elibrary_mcp.client import ELibraryClient
from ferc_elibrary_mcp.server import mcp, set_client
from tests.fixtures import (
    CROSS_DOCKETED_SHEET,
    EMPTY_SEARCH,
    SAMPLE_DOCKET_SHEET,
    SAMPLE_SEARCH,
    search_with_dockets,
    search_with_files,
)
from tests.test_client import DOCKET_URL, SEARCH_URL


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


DATE_ENVELOPE_KEYS = {
    "date_range_applied",
    "date_range_source",
    "date_field_applied",
    "results_may_be_date_limited",
}

# Minimal arguments needed to invoke each date-accepting tool.
TOOL_MIN_ARGS = {
    "search_filings": {"query": "pipeline"},
    "get_docket": {"docket_number": "EL25-49"},
    "collect_related": {"query": "pipeline"},
}


async def test_every_date_accepting_tool_reports_its_window(
    httpx_mock: HTTPXMock, elibrary
):
    """Guard for the defect that reappeared: date logic drifting per tool.

    Any tool that accepts start_date must report which window it applied. This
    walks the registry, so a new search-shaped tool is covered the day it lands.
    """
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH, is_reusable=True)
    httpx_mock.add_response(url=DOCKET_URL, json=SAMPLE_DOCKET_SHEET, is_reusable=True)
    async with Client(mcp) as session:
        tools = await session.list_tools()
        dated = [
            tool
            for tool in tools
            if "start_date" in (tool.inputSchema.get("properties") or {})
        ]
        assert {tool.name for tool in dated} == set(TOOL_MIN_ARGS), (
            "TOOL_MIN_ARGS is out of date; a tool gained or lost start_date"
        )
        for tool in dated:
            result = await session.call_tool(tool.name, TOOL_MIN_ARGS[tool.name])
            missing = DATE_ENVELOPE_KEYS - set(result.data)
            assert not missing, f"{tool.name} omits {sorted(missing)}"
            schema = tool.inputSchema.get("properties") or {}
            assert "date_field" in schema, f"{tool.name} has no date_field parameter"


async def test_pagination_base_is_consistent_across_tools(elibrary):
    """A loop written across tools must not skip or repeat a page.

    get_docket was 0-indexed while search_filings was 1-indexed, which silently
    offset any shared paging loop. Registry-driven so a new paged tool is caught.
    """
    async with Client(mcp) as session:
        tools = await session.list_tools()
    paged = {
        tool.name: (tool.inputSchema.get("properties") or {})["page"]
        for tool in tools
        if "page" in (tool.inputSchema.get("properties") or {})
    }
    assert {"search_filings", "get_docket"} <= set(paged)
    for name, schema in paged.items():
        assert schema.get("default") == 1, f"{name} does not default to page 1"


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


async def test_get_docket_tool_reports_count_basis_and_scope(
    httpx_mock: HTTPXMock, elibrary
):
    httpx_mock.add_response(url=DOCKET_URL, json=CROSS_DOCKETED_SHEET)
    async with Client(mcp) as session:
        result = await session.call_tool("get_docket", {"docket_number": "EL25-49"})
    data = result.data
    assert data["count_basis"] == "distinct_accession"
    assert data["total_hits"] == 2
    assert data["includes_subdockets"] == ["000", "001", "002"]
    # Explains the membership delta against public-only search_filings.
    assert data["availability_scope"] == "all"
    assert data["page_base"] == 1
    assert all("docket_numbers" in f for f in data["filings"])


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
