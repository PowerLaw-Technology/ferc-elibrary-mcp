# Hosted MCP deployment (this repo only)

This document describes how to run the FERC eLibrary MCP server as a **remote HTTP endpoint** and how to use **AWS S3** as the shared document store. Everything is configured via environment variables in this package — no other PowerLaw repositories, databases, or infrastructure are required.

## Phase 2: S3 document store

Point the store at a bucket you control:

```bash
export FERC_STORE_BACKEND=s3
export FERC_STORE_ROOT=s3://your-bucket/ferc-elibrary
# Optional local disk cache for reads/extraction (default: ~/.cache/ferc-elibrary/s3-<hash>)
export FERC_S3_CACHE_DIR=/var/cache/ferc-elibrary
```

Install with the S3 extra:

```bash
uv sync --extra s3
```

AWS credentials use the standard boto3 chain (`AWS_ACCESS_KEY_ID`, `AWS_PROFILE`, instance role, etc.). The server never stores credentials in the repo.

### SharePoint firms (no S3)

Keep `FERC_STORE_BACKEND=local` and point `FERC_STORE_ROOT` at a folder synced from SharePoint/OneDrive. Multiple Claude Desktop installs on the same synced path share the cache.

## Phase 3: Hosted HTTP (Streamable HTTP)

Run the MCP server over HTTP for firm-wide remote access:

```bash
export FERC_MCP_TRANSPORT=http          # alias for streamable-http
export FERC_MCP_HOST=0.0.0.0
export FERC_MCP_PORT=8000
export FERC_MCP_PATH=/mcp

# Bearer tokens per organization (JSON). Required for production HTTP.
export FERC_MCP_AUTH_TOKENS='{"roselle":{"token":"YOUR_SECRET","scopes":["ferc"]}}'

# All firm traffic shares one egress IP — cap global FERC requests:
export FERC_GLOBAL_RATE_LIMIT_RPS=0.5
export FERC_GLOBAL_RATE_LIMIT_BURST=2

# Optional per-org quotas:
export FERC_ORG_RATE_LIMITS='{"roselle":{"rps":0.3,"burst":1}}'

uv run ferc-elibrary-mcp
```

Clients connect to `https://your-host/mcp` with header `Authorization: Bearer YOUR_SECRET`.

### Claude Desktop / Cursor remote connector

Add an MCP server entry with transport URL and bearer token (exact UI varies by client). Example Cursor config:

```json
{
  "mcpServers": {
    "ferc-elibrary": {
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_SECRET"
      }
    }
  }
}
```

## Security notes

- Rotate `FERC_MCP_AUTH_TOKENS` regularly; treat them like API keys.
- Terminate TLS at your reverse proxy (nginx, Caddy, ALB, etc.) — not included in this repo.
- The S3 bucket should be private; grant IAM only to this service role.
- Downloads still land in the configured store (S3 or shared folder), not in chat responses.
