from __future__ import annotations

import re
from pathlib import Path

import pytest

from ferc_elibrary_mcp.client import ELibraryClient


@pytest.mark.live
async def test_live_public_search():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        parsed, hits = await client.search(
            docket="P-15056-000",
            start_date="2020-11-01",
            end_date="2020-12-01",
            limit=5,
        )
    assert parsed.total_hits >= 1
    assert hits
    assert re.fullmatch(r"\d{8}-\d{4}", hits[0].accession_number)


@pytest.mark.live
async def test_live_search_without_docket():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        parsed, hits = await client.search(
            query="pipeline",
            start_date="2026-08-01",
            end_date="2026-08-19",
            limit=5,
        )
    assert parsed.success is True
    assert parsed.total_hits >= 1
    assert hits


@pytest.mark.live
async def test_live_get_filing_by_accession():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        filing = await client.get_filing("20201119-5202")
    assert filing.accession_number == "20201119-5202"
    assert filing.files


@pytest.mark.live
async def test_live_docket_sheet_from_full_number():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        sheet = await client.get_docket("P-15056-000", limit=5)
    assert sheet.total_hits >= 1
    assert sheet.filings
    assert sheet.filings[0].accession_number


@pytest.mark.live
async def test_live_download_public_file(tmp_path):
    async with ELibraryClient(download_dir=tmp_path, rate_limit_seconds=0.5) as client:
        result = await client.download_file("20201119-5202", format="native")
    assert result.skipped is False
    assert result.size > 1000
    assert Path(result.path).exists()


@pytest.mark.live
async def test_live_native_download_is_single_file_not_bundle(tmp_path):
    """20260716-5098 has three attachments; native must return only the SFA."""
    async with ELibraryClient(download_dir=tmp_path, rate_limit_seconds=0.5) as client:
        filing = await client.get_filing("20260716-5098")
        target = next(
            f for f in filing.files if "Shared Facilities Agreement" in f.file_name
        )
        result = await client.download_file(
            "20260716-5098", file_id=target.file_id, format="native"
        )
    assert result.content_type == "application/pdf"
    assert result.is_bundle is False
    assert result.size == target.file_size
    assert result.size_matches_metadata is True
    assert Path(result.path).read_bytes().startswith(b"%PDF")


@pytest.mark.live
async def test_live_phrase_search_beats_term_search():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        loose, _ = await client.search(
            query="master service agreement",
            match="any",
            start_date="2026-01-01",
            end_date="2026-08-19",
            limit=5,
        )
        phrase, _ = await client.search(
            query="master service agreement",
            match="phrase",
            start_date="2026-01-01",
            end_date="2026-08-19",
            limit=5,
        )
        titles, hits = await client.search(
            query="shared facilities agreement",
            match="phrase",
            search_in="description",
            start_date="2026-01-01",
            end_date="2026-08-19",
            limit=5,
        )
    assert phrase.total_hits < loose.total_hits
    assert titles.total_hits < loose.total_hits
    assert any("shared facilities" in h.description.lower() for h in hits)
