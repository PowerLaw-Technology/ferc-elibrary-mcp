from __future__ import annotations

import json
from typing import Any

from fastmcp.server.middleware import Middleware

from ferc_elibrary_mcp.ferc.request_context import current_org_id


class OrgContextMiddleware(Middleware):
    """Attach authenticated org id to the request context for per-org rate limits."""

    async def on_call_tool(self, context: Any, call_next: Any) -> Any:
        token = current_org_id.set(_org_from_context(context))
        try:
            return await call_next(context)
        finally:
            current_org_id.reset(token)


def _org_from_context(context: Any) -> str | None:
    for attr in ("access_token", "token"):
        value = getattr(context, attr, None)
        if value is not None:
            client_id = getattr(value, "client_id", None)
            if client_id:
                return str(client_id)
    request = getattr(context, "request", None)
    if request is not None:
        state = getattr(request, "state", None)
        if state is not None:
            access = getattr(state, "access_token", None)
            if access is not None and getattr(access, "client_id", None):
                return str(access.client_id)
    return None
