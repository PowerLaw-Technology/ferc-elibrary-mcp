from __future__ import annotations

import re

import pytest

from ferc_elibrary_mcp.client import ELibraryClient


@pytest.mark.live
async def test_live_public_search():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        parsed, hits = await client.search(
            docket="ER",
            start_date="2024-01-01",
            end_date="2024-01-31",
            limit=5,
        )
    assert parsed.total_hits >= 1
    assert hits
    assert re.fullmatch(r"\d{8}-\d{4}", hits[0].accession_number)
