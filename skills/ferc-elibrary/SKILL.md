---
name: ferc-elibrary
description: >-
  Workflow for the FERC eLibrary MCP server: search and sync dockets, use the
  document cache, and read large filings in bounded chunks. Use when working
  with FERC eLibrary tools, docket sheets, accession downloads, or regulatory
  filing analysis.
---

# FERC eLibrary MCP workflow

## Principles

1. **Never request full document text.** Large orders and tariffs can exceed a million characters.
2. **Cache before re-fetching.** Check `cached` flags on search and docket results; use `sync_docket` for incremental updates.
3. **Locate, then read.** Use `get_document_outline` and `search_within_document` before `read_document`.

## Standard workflow

### 1. Discover

- `search_filings` for keywords, document types, or parties
- `get_docket` for a full proceeding sheet
- Note `cached: true/false` on each accession

### 2. Populate the store

- `sync_docket` for everything new on a docket (preferred for bulk)
- `download_file` for a single accession or attachment
- `cache_status` to confirm what is already stored

### 3. Read substance (bounded)

For each file you need to analyze:

1. `get_document_outline(accession, filename)` — bookmarks or heuristic sections
2. `search_within_document(accession, filename, query)` — jump to relevant passages
3. `read_document(accession, filename, pages=[...])` or `char_start` / `char_end` — read only what you need
4. If `truncated: true`, continue with `next_char_start` or the next page range

### 4. Summarize large filings

- Summarize **section by section**, not in one pass
- Each section: outline → search → bounded read → short summary
- Merge section summaries at the end

## Anti-patterns

- Do **not** use `get_filing_text` on 100+ page filings (deprecated; bounded and may truncate)
- Do **not** call `read_document` without `pages` or `char_range` on unknown-size documents
- Do **not** re-download accessions already marked `cached: true`

## Configuration (firm shared cache)

Point `FERC_STORE_ROOT` at a folder the whole team can access:

- Local: shared network drive or SharePoint-synced directory (`FERC_STORE_BACKEND=local`)
- AWS: `FERC_STORE_BACKEND=s3` and `FERC_STORE_ROOT=s3://bucket/prefix` (Phase 2)

## Tool quick reference

| Goal | Tool |
|------|------|
| Find filings | `search_filings`, `get_docket` |
| Fill cache | `sync_docket`, `download_file` |
| Inspect cache | `cache_status` |
| Table of contents | `get_document_outline` |
| Find a phrase | `search_within_document` |
| Read a section | `read_document` with bounds |
