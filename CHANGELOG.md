# Changelog

## Unreleased

- Claude Desktop one-click install via an MCP Bundle (`manifest.json`, `mcpb pack`)

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
