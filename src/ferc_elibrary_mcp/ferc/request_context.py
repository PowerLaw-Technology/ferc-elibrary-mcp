from __future__ import annotations

import contextvars

current_org_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ferc_org_id", default=None
)
