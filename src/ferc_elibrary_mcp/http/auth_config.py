from __future__ import annotations

import json
import os
from typing import Any

from fastmcp.server.auth import AccessToken, AuthProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier


def parse_auth_tokens(raw: str) -> dict[str, dict[str, Any]]:
    """Parse FERC_MCP_AUTH_TOKENS JSON.

    Format: {"org-name": {"token": "secret", "scopes": ["ferc"]}, ...}
    """
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("FERC_MCP_AUTH_TOKENS must be a JSON object")
    return data


def build_auth_provider() -> AuthProvider | None:
    raw = os.environ.get("FERC_MCP_AUTH_TOKENS", "").strip()
    if not raw:
        return None
    entries = parse_auth_tokens(raw)
    tokens: dict[str, dict[str, Any]] = {}
    for org_id, meta in entries.items():
        token = meta.get("token")
        if not token:
            continue
        scopes = meta.get("scopes") or ["ferc"]
        if isinstance(scopes, str):
            scopes = [scopes]
        tokens[str(token)] = {
            "client_id": org_id,
            "scopes": list(scopes),
        }
    if not tokens:
        return None
    return StaticTokenVerifier(tokens=tokens, required_scopes=["ferc"])
