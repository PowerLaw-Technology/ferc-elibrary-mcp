from __future__ import annotations

import re
from pathlib import Path

import pytest

from ferc_elibrary_mcp.client import ELibraryClient


@pytest.mark.live
async def test_live_public_search():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        parsed, hits, _ = await client.search(
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
        parsed, hits, _ = await client.search(
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
        loose, _, _l = await client.search(
            query="master service agreement",
            match="any",
            start_date="2026-01-01",
            end_date="2026-08-19",
            limit=5,
        )
        phrase, _, _p = await client.search(
            query="master service agreement",
            match="phrase",
            start_date="2026-01-01",
            end_date="2026-08-19",
            limit=5,
        )
        titles, hits, _ = await client.search(
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


# --- Issue 1: the 60-day default must not truncate docket retrieval ---------
# Counts on active dockets drift as parties file, so these assert >= for totals
# and pin exact accession numbers, which are immutable.

# EL25-49 is a closed set of Commission orders in an FPA 206 show cause docket.
EL25_49_ORDERS = {
    "20250220-3091",  # Order Instituting Proceeding under FPA 206
    "20251218-3081",  # Order on Show Cause, Directing Compliance Filings
    "20260220-3002",  # Notice of Denial of Rehearing by Operation of Law
    "20260618-3103",  # Order on Rehearing, Clarification, and Paper Hearing
}


@pytest.mark.live
async def test_live_docket_query_has_no_date_default():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        parsed, _hits, dates = await client.search(docket="EL25-49", limit=1)
    assert parsed.total_hits >= 309
    assert dates.source == "none"
    assert dates.may_be_date_limited is False


@pytest.mark.live
async def test_live_bare_query_keeps_default_window():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        _parsed, _hits, dates = await client.search(query="interconnection", limit=1)
    assert dates.source == "default_60_day"
    assert dates.may_be_date_limited is True
    assert dates.start is not None


@pytest.mark.live
async def test_live_explicit_dates_are_reported_as_explicit():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        _parsed, _hits, dates = await client.search(
            docket="EL25-49", start_date="2024-01-01", end_date="2026-08-19", limit=1
        )
    assert dates.source == "explicit"


@pytest.mark.live
async def test_live_all_commission_orders_reachable_without_explicit_dates():
    """Regression guard for the discovery path. Fails before the Part A fix."""
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        _parsed, hits, dates = await client.search(
            docket="EL25-49", document_type="Order/Opinion", limit=100
        )
    assert dates.source == "none"
    assert EL25_49_ORDERS <= {h.accession_number for h in hits}


@pytest.mark.live
async def test_live_empty_result_still_reports_date_range():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        parsed, hits, dates = await client.search(query="zzzznonexistentzzzz")
    assert parsed.total_hits == 0
    assert not hits
    assert dates.source == "default_60_day"


@pytest.mark.live
async def test_live_collect_related_reports_date_range():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        collection = await client.collect_related(docket="EL25-49", max_dockets=1)
    assert collection.date_range_source == "none"
    assert collection.results_may_be_date_limited is False


# --- Issue 2: deadlines run from issuance, not filing ----------------------


@pytest.mark.live
async def test_live_issued_date_filter_catches_divergent_filing():
    """20260807-5037 was filed 08/07 but issued 08/06."""
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        _parsed, hits, dates = await client.search(
            docket="ER26-3176",
            start_date="2026-08-06",
            end_date="2026-08-06",
            date_field="issued",
            limit=100,
        )
    assert dates.field == "issued"
    assert "20260807-5037" in {h.accession_number for h in hits}


@pytest.mark.live
async def test_live_filed_date_remains_default():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        _parsed, hits, dates = await client.search(
            docket="ER26-3176",
            start_date="2026-08-06",
            end_date="2026-08-06",
            limit=100,
        )
    assert dates.field == "filed"
    assert "20260807-5037" not in {h.accession_number for h in hits}


# --- Issue 3: sealed counterparts ------------------------------------------


@pytest.mark.live
@pytest.mark.parametrize("accession", ["20260817-5147", "20260710-5118"])
async def test_live_flags_public_redacted_counterpart(accession):
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        filing = await client.get_filing(accession)
    assert filing.has_nonpublic_counterpart is True
    assert filing.nonpublic_counterpart_basis == "file_naming_convention"


@pytest.mark.live
async def test_live_no_false_positive_on_ordinary_filing():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        filing = await client.get_filing("20260716-5098")
    assert filing.has_nonpublic_counterpart is False


# --- Confirmed-working behavior, locked in as regression tests -------------


@pytest.mark.live
async def test_live_pagination_no_overlap_no_gap():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        p1, hits1, _ = await client.search(docket="ER26-3176", limit=25, page=1)
        _p2, hits2, _ = await client.search(docket="ER26-3176", limit=25, page=2)
    a1 = [h.accession_number for h in hits1]
    a2 = [h.accession_number for h in hits2]
    assert len(a1) == 25
    assert p1.total_hits >= 28
    assert not set(a1) & set(a2)
    assert len(set(a1 + a2)) == len(a1) + len(a2)


@pytest.mark.live
async def test_live_limit_above_25_not_silently_capped():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        parsed, hits, _ = await client.search(docket="ER26", limit=100)
    assert len(hits) == 100
    assert parsed.total_hits > 100


@pytest.mark.live
async def test_live_date_boundaries_inclusive_both_ends():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        _parsed, hits, _ = await client.search(
            docket="ER26-3176",
            start_date="2026-08-06",
            end_date="2026-08-06",
            limit=100,
        )
    assert hits
    assert all(h.filed_date == "08/06/2026" for h in hits)


@pytest.mark.live
async def test_live_accepts_iso_and_us_date_formats():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        iso, _, _ = await client.search(
            docket="EL25-49", start_date="2024-01-01", end_date="2026-08-19", limit=1
        )
        us, _, _ = await client.search(
            docket="EL25-49", start_date="01/01/2024", end_date="08/19/2026", limit=1
        )
    assert iso.total_hits == us.total_hits


@pytest.mark.live
async def test_live_rejects_malformed_dates_loudly():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        with pytest.raises(ValueError, match="YYYY-MM-DD or MM/DD/YYYY"):
            await client.search(
                docket="EL25-49", start_date="yesterday", end_date="not-a-date"
            )


@pytest.mark.live
async def test_live_document_type_filter_matches_class_type_prefix():
    async with ELibraryClient(rate_limit_seconds=0.5) as client:
        parsed, hits, _ = await client.search(
            docket="ER26-3111", document_type="Order/Opinion", limit=25
        )
    assert parsed.total_hits >= 1
    assert "20260818-3060" in {h.accession_number for h in hits}


async def _all_docket_rows(client, docket: str) -> list:
    rows = []
    page = 1
    while page <= 20:
        sheet = await client.get_docket(docket, limit=100, page=page)
        if not sheet.filings:
            break
        rows += sheet.filings
        page += 1
    return rows


async def _all_accessions_get_docket(client, docket: str) -> set[str]:
    found: set[str] = set()
    page = 1
    while page <= 20:
        sheet = await client.get_docket(docket, limit=100, page=page)
        if not sheet.filings:
            break
        found |= {f.accession_number for f in sheet.filings}
        page += 1
    return found


async def _all_accessions_search(client, docket: str, *, public_only: bool) -> set[str]:
    found: set[str] = set()
    page = 1
    while page <= 20:
        _parsed, hits, _dates = await client.search(
            docket=docket, limit=100, page=page, public_only=public_only
        )
        if not hits:
            break
        found |= {h.accession_number for h in hits}
        page += 1
    return found


# --- Issue 4: get_docket and search_filings must agree on membership -------


@pytest.mark.live
async def test_live_get_docket_and_search_agree_on_membership():
    """The 71-filing gap was association double counting plus availability.

    get_docket cannot filter on availability (the sheet carries no availability
    code), so it is compared against search with the same scope. Asserting the
    relationship rather than a count, since EL25-49 is live.
    """
    async with ELibraryClient(rate_limit_seconds=0.3) as client:
        sheet_accessions = await _all_accessions_get_docket(client, "EL25-49")
        search_all = await _all_accessions_search(client, "EL25-49", public_only=False)
        search_public = await _all_accessions_search(
            client, "EL25-49", public_only=True
        )
    assert sheet_accessions == search_all
    # Everything search omits under its public-only default is non-public.
    assert search_public <= sheet_accessions


@pytest.mark.live
async def test_live_get_docket_total_matches_retrievable_rows():
    async with ELibraryClient(rate_limit_seconds=0.3) as client:
        sheet = await client.get_docket("EL25-49", limit=100, page=1)
        retrievable = await _all_accessions_get_docket(client, "EL25-49")
    assert sheet.total_hits == len(retrievable)
    assert sheet.count_basis == "distinct_accession"


@pytest.mark.live
async def test_live_get_docket_returns_no_duplicate_rows():
    async with ELibraryClient(rate_limit_seconds=0.3) as client:
        rows: list[str] = []
        page = 1
        while page <= 20:
            sheet = await client.get_docket("EL25-49", limit=100, page=page)
            if not sheet.filings:
                break
            rows += [f.accession_number for f in sheet.filings]
            page += 1
    assert len(rows) == len(set(rows))


@pytest.mark.live
async def test_live_get_docket_reports_count_basis_and_subdockets():
    async with ELibraryClient(rate_limit_seconds=0.3) as client:
        sheet = await client.get_docket("EL25-49", limit=5)
    assert sheet.count_basis in ("distinct_accession", "docket_association")
    assert sheet.includes_subdockets
    assert sheet.availability_scope == "all"


@pytest.mark.live
async def test_live_get_docket_rows_carry_full_docket_array():
    async with ELibraryClient(rate_limit_seconds=0.3) as client:
        rows = await _all_docket_rows(client, "EL25-49")
    assert rows
    assert all(f.docket_numbers for f in rows)
    # EL25-49 is consolidated, so some filings carry several associations.
    assert any(len(f.docket_numbers) > 1 for f in rows)


@pytest.mark.live
async def test_live_known_order_is_cross_docketed():
    """20251218-3081 is captioned to both EL25-49-000 and -001.

    Scanned across pages rather than pinned to one, since a filing's page
    position moves as the docket grows.
    """
    async with ELibraryClient(rate_limit_seconds=0.3) as client:
        rows = await _all_docket_rows(client, "EL25-49")
    row = next((f for f in rows if f.accession_number == "20251218-3081"), None)
    assert row is not None
    assert row.docket_numbers == ["EL25-49-000", "EL25-49-001"]


# --- Issue 5: get_docket must report its window ----------------------------


@pytest.mark.live
async def test_live_get_docket_reports_date_envelope():
    async with ELibraryClient(rate_limit_seconds=0.3) as client:
        sheet = await client.get_docket("EL25-49", limit=5)
    assert sheet.date_range_source == "none"
    assert sheet.results_may_be_date_limited is False
    assert sheet.date_range_applied == {"start": None, "end": None}


@pytest.mark.live
async def test_live_get_docket_supports_issued_date_field():
    async with ELibraryClient(rate_limit_seconds=0.3) as client:
        sheet = await client.get_docket(
            "ER26-3176",
            start_date="2026-08-06",
            end_date="2026-08-06",
            date_field="issued",
            limit=100,
        )
    assert sheet.date_field_applied == "issued"
    assert sheet.date_field_filtered_client_side is True
    assert "20260807-5037" in {f.accession_number for f in sheet.filings}


@pytest.mark.live
async def test_live_get_docket_filed_field_excludes_divergent_filing():
    async with ELibraryClient(rate_limit_seconds=0.3) as client:
        sheet = await client.get_docket(
            "ER26-3176", start_date="2026-08-06", end_date="2026-08-06", limit=100
        )
    assert sheet.date_field_applied == "filed"
    assert sheet.date_field_filtered_client_side is False
    assert "20260807-5037" not in {f.accession_number for f in sheet.filings}


@pytest.mark.live
async def test_live_get_docket_suppresses_null_issued_sentinel():
    """The sheet returns 0001-01-01 for every issuance date."""
    async with ELibraryClient(rate_limit_seconds=0.3) as client:
        sheet = await client.get_docket("EL25-49", limit=25)
    assert all(f.issued_date == "" for f in sheet.filings)


@pytest.mark.live
async def test_live_get_docket_dates_match_search_format():
    async with ELibraryClient(rate_limit_seconds=0.3) as client:
        sheet = await client.get_docket(
            "ER26-3176", start_date="2026-08-06", end_date="2026-08-06", limit=25
        )
        _parsed, hits, _dates = await client.search(
            docket="ER26-3176",
            start_date="2026-08-06",
            end_date="2026-08-06",
            limit=25,
        )
    assert {f.filed_date for f in sheet.filings} == {h.filed_date for h in hits}


# --- Issue 6: pagination base and ordering --------------------------------


@pytest.mark.live
async def test_live_get_docket_pagination_is_one_indexed():
    async with ELibraryClient(rate_limit_seconds=0.3) as client:
        page1 = await client.get_docket("EL25-49", limit=3, page=1)
        page0 = await client.get_docket("EL25-49", limit=3, page=0)
        default = await client.get_docket("EL25-49", limit=3)
        page2 = await client.get_docket("EL25-49", limit=3, page=2)
    first = [f.accession_number for f in page1.filings]
    assert page1.page_base == 1
    assert [f.accession_number for f in page0.filings] == first
    assert [f.accession_number for f in default.filings] == first
    assert not set(first) & {f.accession_number for f in page2.filings}


@pytest.mark.live
async def test_live_get_docket_sort_order():
    async with ELibraryClient(rate_limit_seconds=0.3) as client:
        oldest = await client.get_docket("EL25-49", limit=3)
        newest = await client.get_docket("EL25-49", limit=3, sort_order="newest_first")
    assert oldest.sort_order == "oldest_first"
    assert oldest.total_hits == newest.total_hits
    first_old = oldest.filings[0].accession_number
    first_new = newest.filings[0].accession_number
    assert first_old != first_new
    # 20241004-4000 is the earliest filing in the proceeding.
    assert first_old == "20241004-4000"


@pytest.mark.live
async def test_live_single_file_download_is_not_a_bundle(tmp_path):
    async with ELibraryClient(download_dir=tmp_path, rate_limit_seconds=0.5) as client:
        result = await client.download_file(
            "20260716-5098", file_id="77602C02-8449-CD32-8585-9F6C2BE00000"
        )
    assert result.size == 1464398
    assert result.content_type == "application/pdf"
    assert result.is_bundle is False
    assert result.size_matches_metadata is True
