# FERC eLibrary MCP

A [Model Context Protocol](https://modelcontextprotocol.io/) server that lets Claude Desktop search the public [FERC eLibrary](https://elibrary.ferc.gov/eLibrary/search), inspect docket sheets, and download public filings.

FERC does not publish an official eLibrary developer API. This server talks to the same JSON backend the public website uses (`https://elibrary.ferc.gov/eLibrarywebapi/api/`). That interface is undocumented and can change.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [Claude Desktop](https://claude.ai/download)

## Install

```bash
git clone <this-repo>
cd ferc-elibrary-mcp
uv sync
```

## Tools

| Tool | Purpose |
| --- | --- |
| `search_filings` | Keyword, docket, accession, document type, category, and industry search. Public filings only. See [Date filtering](#date-filtering) for how the date window is chosen. |
| `get_docket` | Docket sheet: related filings, applicants, accession numbers. See [Docket sheets vs search](#docket-sheets-vs-search) for how it differs from `search_filings`. |
| `get_filing` | Metadata for one accession (`YYYYMMDD-NNNN`). |
| `list_files` | Files attached to an accession (call before downloading). |
| `download_file` | Save a **public** single file, accession zip, or generated PDF under `FERC_DOWNLOAD_DIR`. Does not return bytes. |
| `collect_related` | Search a term or document type, then group related filings by docket (capped at 10 dockets × 50 filings). Optional downloads: 10 public files, skip over 25 MB. |

Privileged, protected, and CEII documents are refused.

## Date filtering

Date defaults are scope-aware, because a 60-day window layered on a named docket silently hides most of a proceeding:

| Call | Window applied | `date_range_source` |
| --- | --- | --- |
| `docket=` or `accession_number=` | none, whole proceeding | `none` |
| open-ended query, no dates | last 60 days | `default_60_day` |
| any explicit `start_date`/`end_date` | as given | `explicit` |

Every date-accepting tool — `search_filings`, `collect_related`, and `get_docket` — reports `date_range_applied`, `date_range_source`, `date_field_applied`, `results_may_be_date_limited`, and `date_field_filtered_client_side`, including on empty results, since an empty set under an unnoticed default is the case most likely to mislead. Treat `total_hits` as a complete count only when `results_may_be_date_limited` is false.

All three resolve their window through a single `resolve_date_range` helper and report it via `DateRangeResolution.as_envelope()`. A registry test walks the tool list and fails if any tool accepting `start_date` omits the envelope or a `date_field` parameter, so a new search-shaped tool is covered the day it is added.

`date_field` selects which date the range filters on, `filed` (default) or `issued`. Use `issued` for deadline arithmetic: FPA 313(a) rehearing and most Commission-set comment and compliance clocks run from issuance, and the two dates diverge. On `ER26-3176`, accession `20260807-5037` was filed 08/07 but issued 08/06, so a filed-date search for 08/06 misses it. Both are filtered server-side by eLibrary, so paging stays exact.

## Docket sheets vs search

`get_docket` and `search_filings` cover the same filings but reach them differently, and the differences are reported rather than left to be discovered:

- **One row per filing.** eLibrary returns one row per *docket association*, so a pleading captioned to `-000`, `-001`, and `-002` arrives three times and its `totalHits` counts it three times. Rows are merged on accession number, every association is preserved in `docket_numbers`, and `count_basis` reports `distinct_accession`. On EL25-49 that is the difference between FERC's reported 380 and the 312 filings you can actually retrieve.
- **Paging is client-side.** `numHits` and `pageNumber` do not slice the sheet reliably — rows per page exceed the requested limit and later pages overlap — so the sheet is fetched once and paginated locally. `page` is 1-indexed on both tools; `page=0` is accepted as page 1.
- **Availability.** The sheet carries no availability code, so `get_docket` cannot filter on it and reports `availability_scope: "all"`. `search_filings` is public-only by default. A docket sheet may therefore list a few privileged filings that search omits; on EL25-49 that is 3 of 312.
- **Ordering.** `get_docket` returns oldest-first (chronological, like a docket sheet), `search_filings` newest-first. Pass `sort_order="newest_first"` to align them.
- **Issuance dates.** The sheet reports every `issued_date` as the .NET null sentinel `0001-01-01`, so it is surfaced as an empty string rather than a date in year 1. `date_field="issued"` on `get_docket` resolves the window through the search endpoint, which carries real issuance dates, and sets `date_field_filtered_client_side: true`.

## Sealed counterparts

`get_filing` and `list_files` report `has_nonpublic_counterpart`, a signal that a sealed, protected, or CEII version likely exists on the same accession — the thing you would move for access to under 18 C.F.R. 388.113. It is inferred from filer naming convention (a file name or description prefixed `PUBLIC`, or containing `REDACTED`), so `nonpublic_counterpart_basis` reports `file_naming_convention` to mark it as heuristic rather than authoritative. Utility names such as "Public Service Company" are excluded to avoid false positives. No protected content is ever returned, and the signal is deliberately absent from `search_filings` results.

## Search precision

eLibrary treats a bare multi-word query as independent terms, which buries the filings that actually contain the phrase. Two parameters control this:

- `match`: `phrase` (default) requires the exact phrase, `all` requires every term, `any` is FERC's loose term matching.
- `search_in`: `both` (default) searches descriptions and full document text, `description` matches only the filing title, `full_text` only the document body.

Searching `shared facilities agreement` over 2026 filings:

| `match` | `search_in` | Hits |
| --- | --- | --- |
| `any` | `both` | 5,627 |
| `phrase` | `both` | 324 |
| `phrase` | `description` | 65 |

Use `search_in="description"` when a phrase search still returns too much noise; full-text matching finds any passing mention deep inside an attachment. eLibrary syntax you write yourself (quotes, `AND`, `OR`, `NOT`, `NEAR`) is forwarded unchanged.

## Download formats

`download_file` takes a `format`:

- `native` (default) saves the one file identified by `file_id`.
- `zip` bundles every file on the accession.
- `pdf` asks eLibrary to generate a combined PDF of the accession.

eLibrary labels every download `application/octet-stream`, so the real type is inferred from magic bytes and the file extension. Results also report `expected_size` from FERC's metadata next to the bytes actually written, plus `size_matches_metadata` and `is_bundle`, so receiving a bundle when you asked for one file is visible rather than silent.

## Test with MCP Inspector

From the project directory:

```bash
npx @modelcontextprotocol/inspector uv run ferc-elibrary-mcp
```

Call `search_filings` with `docket` `P-15056-000` and a `start_date` / `end_date` around `2020-11-19` to confirm a known public hit.

## Claude Desktop

Add the server to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ferc-elibrary": {
      "command": "/Users/zoschin/Projects/ferc-elibrary-mcp/.venv/bin/ferc-elibrary-mcp",
      "args": [],
      "env": {
        "FERC_DOWNLOAD_DIR": "/Users/zoschin/Downloads/ferc-elibrary",
        "FERC_MCP_IDLE_TIMEOUT_SECONDS": "14400"
      }
    }
  }
}
```

Use the absolute path to the venv entry point rather than `uv`. Claude Desktop is a GUI app and does not inherit your shell `PATH`, so a bare `uv` resolves only if it happens to sit in a system directory; on a Homebrew install it does not.

Fully quit and reopen Claude Desktop. Confirm the server under **Settings → Developer**.

Downloads default to `~/Downloads/ferc-elibrary` if `FERC_DOWNLOAD_DIR` is unset. See [Orphaned server processes](#orphaned-server-processes) for `FERC_MCP_IDLE_TIMEOUT_SECONDS`; remove it to restore the default of never self-terminating.

## Example prompts

- Search eLibrary for comments and protests about the Ashokan pumped storage project in the last year.
- Pull the docket sheet for CP21-470 and list related filings.
- Find Order/Opinion issuances in the electric industry from January 2024 and collect related docket filings.
- Download the public PDF for accession 20201119-5202.

## Tests

```bash
uv run pytest
uv run pytest -m live   # optional smoke test against the live public API
```

## Limits

## Orphaned server processes

Claude Desktop occasionally spawns two stdio servers within a second of each other and talks to only one. It does not close stdin on the abandoned instance, so that process never sees EOF and idles forever — one leaked pair per day in practice, and tool calls that get routed to a stale instance hang until the client's own timeout rather than failing.

The server itself is not at fault: it exits cleanly on stdin EOF (exit code 0) and on `SIGTERM`. An abandoned instance simply has no way to notice nobody is listening.

Set `FERC_MCP_IDLE_TIMEOUT_SECONDS` to have an instance that has received no messages for that long shut itself down via `SIGTERM`. Any request resets the timer, so an in-use server is unaffected; only a fully abandoned one is reaped. It is **disabled by default** (`0`), because a healthy-but-unused server would also exit and recovery then depends on the client respawning it. The Claude Desktop config below sets 4 hours, comfortably longer than any gap in an active session.

To check for and clear strays by hand:

```bash
ps -eo pid,etime,command | grep '[f]erc-elibrary-mcp'
kill -TERM <pid>   # they are idle, not wedged; no -9 needed
```

- Public documents only. No FERC login, CEII, privileged, or protected files.
- File bytes are written to disk, not sent back to Claude.
- `collect_related` caps how many dockets and files it will pull so a broad query cannot dump thousands of filings into context.
- The backend is undocumented and sits behind a proxy that intermittently returns 502/503/520. Transient 5xx responses are retried up to three times with backoff.
- FERC returns HTTP 200 with `success: false` and a .NET exception string for some malformed payloads. Those are raised as errors rather than silently returning zero hits.
