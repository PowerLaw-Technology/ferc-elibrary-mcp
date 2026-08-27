# Changelog

## Unreleased

- `download_file(format=zip)` unwraps single-member archives and refreshes `content_type` / `is_bundle` / `expected_size` for the saved file (including bare `.docx` OOXML)
- Prefer OOXML extensions over ZIP magic when sniffing content types
- `download_bundle.skipped_accessions` now includes `reason` and `category` (`restricted` vs `not_found`)

## 0.1.1 — 2026-08-27

- Expand unexpanded MCPB placeholders (`${HOME}`, `~`, `${DOWNLOADS}`) in `FERC_DOWNLOAD_DIR` so Claude Desktop downloads land in the real Downloads folder
- `download_bundle` skips accessions omitted from public search (privileged/CEII) instead of aborting the batch with a "not found" error

## 0.1.0 — 2026-08-26

Initial release: MCP server and async Python client for the public FERC eLibrary JSON API.

- Tools: `search_filings`, `get_docket`, `get_filing`, `list_files`, `download_file`, `download_bundle`, `collect_related`
- `download_bundle`: one Zip & Download request for many files/accessions, rewritten into per-accession folders
- Scope-aware date filtering (no silent 60-day truncation on named dockets/accessions)
- Docket sheet deduplication, client-side paging, and issued-date cross-reference
- Phrase / all / any search modes and description vs full-text scope
- Public-only downloads (`native` / `zip` / `pdf`) with content-type inference
- Heuristic `has_nonpublic_counterpart` signal (no CEII/privileged content)
- Optional idle watchdog (`FERC_MCP_IDLE_TIMEOUT_SECONDS`) for abandoned stdio instances
- Install via `uvx` from git or clone + `uv sync` for development
