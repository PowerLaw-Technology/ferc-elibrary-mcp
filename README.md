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
| `search_filings` | Keyword, docket, accession, document type, category, and industry search. Public filings only. Defaults to the last 60 filed days. |
| `get_docket` | Docket sheet: related filings, applicants, accession numbers. |
| `get_filing` | Metadata for one accession (`YYYYMMDD-NNNN`). |
| `list_files` | Files attached to an accession (call before downloading). |
| `download_file` | Save a **public** native file or generated PDF under `FERC_DOWNLOAD_DIR`. Does not return bytes. |
| `collect_related` | Search a term or document type, then group related filings by docket (capped at 10 dockets × 50 filings). Optional downloads: 10 public files, skip over 25 MB. |

Privileged, protected, and CEII documents are refused.

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
      "command": "uv",
      "args": [
        "--directory",
        "/Users/zoschin/Projects/ferc-elibrary-mcp",
        "run",
        "ferc-elibrary-mcp"
      ],
      "env": {
        "FERC_DOWNLOAD_DIR": "/Users/zoschin/Downloads/ferc-elibrary"
      }
    }
  }
}
```

Fully quit and reopen Claude Desktop. Confirm the server under **Settings → Developer**.

Downloads default to `~/Downloads/ferc-elibrary` if `FERC_DOWNLOAD_DIR` is unset.

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

- Public documents only. No FERC login, CEII, privileged, or protected files.
- File bytes are written to disk, not sent back to Claude.
- `collect_related` caps how many dockets and files it will pull so a broad query cannot dump thousands of filings into context.
