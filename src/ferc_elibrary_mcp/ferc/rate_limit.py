from __future__ import annotations

import asyncio
import time


class TokenBucketLimiter:
    """Async token-bucket rate limiter for FERC API calls."""

    def __init__(self, rps: float, *, burst: int = 2) -> None:
        self._rps = max(0.0, rps)
        self._burst = max(1, burst)
        self._tokens = float(self._burst)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._rps <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._updated
            self._updated = now
            self._tokens = min(self._burst, self._tokens + elapsed * self._rps)
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rps
                await asyncio.sleep(wait)
                self._tokens = 0.0
                self._updated = time.monotonic()
            else:
                self._tokens -= 1.0
