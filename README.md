# FERC eLibrary MCP

A [Model Context Protocol](https://modelcontextprotocol.io/) server and async Python library for searching the public [FERC eLibrary](https://elibrary.ferc.gov/eLibrary/search), inspecting docket sheets, and downloading public filings. Works with any MCP client (Claude Desktop, Cursor, Claude Code, and others).

## Disclaimer

FERC does not publish an official eLibrary developer API. This project talks to the same undocumented JSON backend the public website uses (`https://elibrary.ferc.gov/eLibrarywebapi/api/`). That interface can change without notice.

- Public documents only — no FERC login, CEII, privileged, or protected content
- Be polite about rate limits; the client spaces requests by default
- Use this for research against publicly available filings, not as a substitute for official access procedures

## Install in Claude Desktop (easiest)

No Python, terminal, or JSON. Claude Desktop installs the server for you.

1. Install [Claude Desktop](https://claude.ai/download).
2. Download `ferc-elibrary.mcpb` from the [latest GitHub Release](https://github.com/PowerLaw-Technology/ferc-elibrary-mcp/releases/latest).
3. Double-click the file, or drag it into Claude Desktop → **Settings → Extensions**.
4. Click **Install**. Leave the download folder as-is unless you want PDFs somewhere else.
5. Ask Claude in plain language, for example:
   - Search eLibrary for comments and protests about the Ashokan pumped storage project in the last year.
   - Pull the docket sheet for CP21-470 and list related filings.
   - Download the public PDF for accession 20201119-5202.

The first launch may take a minute while Claude installs Python via [uv](https://docs.astral.sh/uv/). After that it starts quickly. Downloaded files go in `Downloads/ferc-elibrary` (or the folder you picked). Public filings only.

If a release is not up yet, a maintainer can build the same file with:

```bash
npx --yes @anthropic-ai/mcpb pack . dist/ferc-elibrary.mcpb
```

Then email or AirDrop `dist/ferc-elibrary.mcpb`.

## Requirements

- **Claude Desktop extension:** none on your machine (Claude manages Python via uv)
- **uvx / library / contributors:** Python 3.12+ and [uv](https://docs.astral.sh/uv/)

## Install

### End users (other MCP clients)

No clone required. Clients launch the server with `uvx` from git (see [MCP client configuration](#mcp-client-configuration)). Replace `OWNER` with the GitHub owner once the repo is published:

```bash
uvx --from git+https://github.com/OWNER/ferc-elibrary-mcp ferc-elibrary-mcp
```

### Contributors

```bash
git clone https://github.com/OWNER/ferc-elibrary-mcp
cd ferc-elibrary-mcp
uv sync
```

## Library usage

`ELibraryClient` is an async context manager. Use it from your own code without starting the MCP server:

```python
import asyncio
from ferc_elibrary_mcp import ELibraryClient


async def main() -> None:
    async with ELibraryClient() as client:
        raw, summaries, dates = await client.search(
            query="shared facilities agreement",
            match="phrase",
        )
        print(raw.total_hits, dates.source, len(summaries))
        if summaries:
            filing = await client.get_filing(summaries[0].accession_number)
            print(filing.description, filing.url)


asyncio.run(main())
```

Downloads and extracted text are stored under `FERC_STORE_ROOT` (default `~/Downloads/ferc-elibrary`). `FERC_DOWNLOAD_DIR` is a legacy alias. Rate limit: `FERC_RATE_LIMIT_RPS` (default `0.5`, burst `2`).

## Tools

| Tool | Purpose |
| --- | --- |
| `search_filings` | Keyword, docket, accession, document type, category, and industry search. Includes `cached` per hit. |
| `get_docket` | Docket sheet with `cached` per accession. |
| `get_filing` / `list_files` | Metadata for one accession, including cache and extraction stats. |
| `sync_docket` | Incrementally fetch accessions missing from the store. |
| `cache_status` | Inspect what the store holds. |
| `read_document` | **Bounded** plain text from a cached attachment (pages or char range). |
| `search_within_document` | Find passages with page/char offsets. |
| `get_document_outline` | PDF bookmarks or heuristic section map. |
| `get_filing_text` | **Deprecated** bounded alias for `read_document`. |
| `download_file` | Cache a public file to the store (never returns text). |
| `download_bundle` | Zip & Download many public files; only cache misses hit FERC. |
| `collect_related` | Search and group related filings by docket. |

See [`skills/ferc-elibrary/SKILL.md`](skills/ferc-elibrary/SKILL.md) for the recommended large-document workflow.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `FERC_STORE_BACKEND` | `local` | Store backend (`local` or `s3`) |
| `FERC_STORE_ROOT` | `~/Downloads/ferc-elibrary` | Local path or `s3://bucket/prefix` |
| `FERC_DOWNLOAD_DIR` | same as above | Legacy alias for store root |
| `FERC_S3_CACHE_DIR` | auto | Local disk cache when using S3 |
| `FERC_RATE_LIMIT_RPS` | `0.5` | Token-bucket requests per second |
| `FERC_RATE_LIMIT_BURST` | `2` | Burst capacity |
| `FERC_MAX_READ_CHARS` | `25000` | Per-call text cap for `read_document` |
| `FERC_ENABLE_OCR` | `false` | Opt-in OCR for scanned PDFs (not yet implemented) |
| `FERC_MCP_TRANSPORT` | `stdio` | `stdio` (desktop) or `http` / `streamable-http` (hosted) |
| `FERC_MCP_HOST` / `PORT` / `PATH` | `0.0.0.0` / `8000` / `/mcp` | HTTP bind settings |
| `FERC_MCP_AUTH_TOKENS` | — | JSON map of org → bearer token (required for HTTP) |
| `FERC_GLOBAL_RATE_LIMIT_RPS` | `0` | Firm-wide FERC egress cap (hosted mode) |
| `FERC_ORG_RATE_LIMITS` | — | Per-org FERC quotas (JSON) |

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for S3 and hosted HTTP setup (this repo only — no external PowerLaw infra).

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

`download_file` takes a `format` for a **single accession**:

- `native` (default) saves the one file identified by `file_id`.
- `zip` bundles every file on that accession.
- `pdf` asks eLibrary to generate a combined PDF of the accession.

For **many files or many accessions**, use `download_bundle` instead. It calls the same Zip & Download endpoint the eLibrary UI uses when you fill the green zip folder — one HTTP request with a list of file IDs — rather than N× (`get_filing` + download + rate-limit wait). Pass any mix of `accession_numbers`, `file_ids`, and/or `docket`. By default the flat FERC names (`20260716-5098_Agreement.pdf`) are rewritten into folders (`20260716-5098/Agreement.pdf`). Caps default to 100 files / 500 MB (`FERC_MAX_BUNDLE_FILES`, `FERC_MAX_BUNDLE_BYTES`); raise `FERC_BUNDLE_TIMEOUT_SECONDS` (default 300) for very large archives.

`collect_related(..., download=True)` uses that bulk path and returns a `bundle` field pointing at the archive.

eLibrary labels every download `application/octet-stream`, so the real type is inferred from magic bytes and the file extension (OOXML extensions win over ZIP magic, since `.docx` is itself a ZIP). Single-file results also report `expected_size` from FERC's metadata next to the bytes actually written, plus `size_matches_metadata` and `is_bundle`, so receiving a bundle when you asked for one file is visible rather than silent. `format=zip` on a one-file accession unwraps to that file and updates those fields to match what was saved.

## Build the Claude Desktop bundle

From a clone, with Node.js 18+ available:

```bash
npx --yes @anthropic-ai/mcpb validate manifest.json
npx --yes @anthropic-ai/mcpb pack . dist/ferc-elibrary.mcpb
```

The bundle uses `server.type = "uv"`: it ships source and `pyproject.toml`, not a vendored virtualenv. Claude Desktop downloads Python and dependencies on first run. CI packs the same file on every push and attaches it to GitHub Releases.

## MCP client configuration

Claude Desktop users should prefer the [one-click `.mcpb` install](#install-in-claude-desktop-easiest). The JSON below is for Cursor, Claude Code, and other clients.

Replace `OWNER` with the GitHub owner of this repository. All snippets use portable `uvx` from git — no absolute machine paths.

Downloads default to `~/Downloads/ferc-elibrary` if `FERC_DOWNLOAD_DIR` is unset. Set `FERC_MCP_IDLE_TIMEOUT_SECONDS` to reap abandoned stdio instances (see [Orphaned server processes](#orphaned-server-processes)); omit it or use `0` to never self-terminate (the default).

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or the equivalent Claude Desktop config on your OS:

```json
{
  "mcpServers": {
    "ferc-elibrary": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/OWNER/ferc-elibrary-mcp",
        "ferc-elibrary-mcp"
      ],
      "env": {
        "FERC_DOWNLOAD_DIR": "/Users/YOU/Downloads/ferc-elibrary",
        "FERC_MCP_IDLE_TIMEOUT_SECONDS": "14400"
      }
    }
  }
}
```

Use an absolute path for `FERC_DOWNLOAD_DIR` (expand `~` yourself). Claude Desktop is a GUI app and may not expand `~` or inherit your shell `PATH`; ensure `uvx` is on a PATH the app can see (for example by installing uv system-wide or wrapping with the full path to `uvx`).

Fully quit and reopen Claude Desktop. Confirm the server under **Settings → Developer**.

### Cursor

Add to `.cursor/mcp.json` in a project, or your user MCP config:

```json
{
  "mcpServers": {
    "ferc-elibrary": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/OWNER/ferc-elibrary-mcp",
        "ferc-elibrary-mcp"
      ],
      "env": {
        "FERC_DOWNLOAD_DIR": "/Users/YOU/Downloads/ferc-elibrary",
        "FERC_MCP_IDLE_TIMEOUT_SECONDS": "14400"
      }
    }
  }
}
```

### Claude Code

Project scope (`.mcp.json` at the project root) or user scope (`claude mcp add` / `~/.claude.json`):

```json
{
  "mcpServers": {
    "ferc-elibrary": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/OWNER/ferc-elibrary-mcp",
        "ferc-elibrary-mcp"
      ],
      "env": {
        "FERC_DOWNLOAD_DIR": "${HOME}/Downloads/ferc-elibrary",
        "FERC_MCP_IDLE_TIMEOUT_SECONDS": "14400"
      }
    }
  }
}
```

Or via CLI:

```bash
claude mcp add --scope user ferc-elibrary -- \
  uvx --from git+https://github.com/OWNER/ferc-elibrary-mcp ferc-elibrary-mcp
```

## Example prompts

- Search eLibrary for comments and protests about the Ashokan pumped storage project in the last year.
- Pull the docket sheet for CP21-470 and list related filings.
- Find Order/Opinion issuances in the electric industry from January 2024 and collect related docket filings.
- Download the public PDF for accession 20201119-5202.

## Test with MCP Inspector

From a clone of the project:

```bash
npx @modelcontextprotocol/inspector uv run ferc-elibrary-mcp
```

Call `search_filings` with `docket` `P-15056-000` and a `start_date` / `end_date` around `2020-11-19` to confirm a known public hit.

## Tests

```bash
uv run pytest
uv run pytest -m live   # optional smoke test against the live public API
```

## Limits

- Public documents only. No FERC login, CEII, privileged, or protected files.
- File bytes are written to disk, not returned through the MCP tool response.
- `collect_related` caps how many dockets and files it will pull so a broad query cannot dump thousands of filings into context.
- The backend is undocumented and sits behind a proxy that intermittently returns 502/503/520. Transient 5xx responses are retried up to three times with backoff.
- FERC returns HTTP 200 with `success: false` and a .NET exception string for some malformed payloads. Those are raised as errors rather than silently returning zero hits.

### Orphaned server processes

Some MCP clients (notably Claude Desktop) occasionally spawn two stdio servers within a second of each other and talk to only one. They may not close stdin on the abandoned instance, so that process never sees EOF and idles forever — one leaked pair per day in practice, and tool calls that get routed to a stale instance hang until the client's own timeout rather than failing.

The server itself is not at fault: it exits cleanly on stdin EOF (exit code 0) and on `SIGTERM`. An abandoned instance simply has no way to notice nobody is listening.

Set `FERC_MCP_IDLE_TIMEOUT_SECONDS` to have an instance that has received no messages for that long shut itself down via `SIGTERM`. Any request resets the timer, so an in-use server is unaffected; only a fully abandoned one is reaped. It is **disabled by default** (`0`), because a healthy-but-unused server would also exit and recovery then depends on the client respawning it. The sample configs above set 4 hours, comfortably longer than any gap in an active session.

To check for and clear strays by hand:

```bash
ps -eo pid,etime,command | grep '[f]erc-elibrary-mcp'
kill -TERM <pid>   # they are idle, not wedged; no -9 needed
```

## License

MIT — see [LICENSE](LICENSE).
