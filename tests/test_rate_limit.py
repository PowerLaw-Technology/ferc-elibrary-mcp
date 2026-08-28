from __future__ import annotations

import httpx
import pytest

from ferc_elibrary_mcp.exceptions import RateLimitError
from ferc_elibrary_mcp.ferc.client import FercClient


async def test_429_raises_no_retry_storm(httpx_mock) -> None:
    client = FercClient(rps=0)
    httpx_mock.add_response(url="https://elibrary.ferc.gov/eLibrarywebapi/api/Search/AdvancedSearch", status_code=429)
    with pytest.raises(RateLimitError):
        await client.request_json("POST", "Search/AdvancedSearch", json={})
