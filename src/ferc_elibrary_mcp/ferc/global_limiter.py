from __future__ import annotations

import json
from typing import Any

from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.ferc.rate_limit import TokenBucketLimiter
from ferc_elibrary_mcp.ferc.request_context import current_org_id

_global_limiter: TokenBucketLimiter | None = None
_org_limiters: dict[str, TokenBucketLimiter] = {}


def global_limiter() -> TokenBucketLimiter | None:
    global _global_limiter
    if config.GLOBAL_RATE_LIMIT_RPS <= 0:
        return None
    if _global_limiter is None:
        _global_limiter = TokenBucketLimiter(
            config.GLOBAL_RATE_LIMIT_RPS,
            burst=config.GLOBAL_RATE_LIMIT_BURST,
        )
    return _global_limiter


def org_limiter(org_id: str) -> TokenBucketLimiter | None:
    spec = config.ORG_RATE_LIMITS.get(org_id)
    if not spec:
        return None
    if org_id not in _org_limiters:
        _org_limiters[org_id] = TokenBucketLimiter(
            float(spec.get("rps", config.RATE_LIMIT_RPS)),
            burst=int(spec.get("burst", config.RATE_LIMIT_BURST)),
        )
    return _org_limiters[org_id]


async def throttle_ferc(org_id: str | None = None) -> None:
    """Apply global and per-org FERC rate limits (hosted HTTP mode)."""
    gl = global_limiter()
    if gl is not None:
        await gl.acquire()
    org = org_id or current_org_id.get()
    if org:
        ol = org_limiter(org)
        if ol is not None:
            await ol.acquire()


def parse_org_rate_limits(raw: str) -> dict[str, dict[str, Any]]:
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("FERC_ORG_RATE_LIMITS must be a JSON object")
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}
