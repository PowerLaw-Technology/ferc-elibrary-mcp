# Changelog

## Unreleased

## 0.3.0 — 2026-08-28

- **Phase 2:** S3 document store (`FERC_STORE_BACKEND=s3`, `FERC_STORE_ROOT=s3://bucket/prefix`); optional `boto3` extra
- **Phase 3:** Streamable HTTP transport (`FERC_MCP_TRANSPORT=http`), bearer-token auth per org (`FERC_MCP_AUTH_TOKENS`), global and per-org FERC rate limits
- Deployment guide: `docs/DEPLOYMENT.md` (self-contained in this repo)

## 0.2.0 — 2026-08-28

- **v2 architecture:** FERC HTTP client with token-bucket rate limiting and HTTP 429 hard-backoff; document store with manifests and docket indexes; cache-first downloads
- **New tools:** `read_document`, `search_within_document`, `get_document_outline`, `sync_docket`, `cache_status`
- **Large documents:** extract-once sidecars (`.extracted.txt`, `.pages.json`); bounded reads with explicit truncation metadata
- `get_filing_text` deprecated as bounded alias for `read_document`
- Config: `FERC_STORE_BACKEND`, `FERC_STORE_ROOT`, `FERC_RATE_LIMIT_RPS`, `FERC_MAX_READ_CHARS` (`FERC_DOWNLOAD_DIR` still supported)
- Companion skill at `skills/ferc-elibrary/SKILL.md`

## 0.1.3 — 2026-08-27

- `get_filing_text`: download a public attachment and return extracted plain text (PDF/DOCX/text) so agents can summarize filings without filesystem access

## 0.1.2 — 2026-08-27

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
