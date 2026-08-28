from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.exceptions import ELibraryRequestError, RateLimitError
from ferc_elibrary_mcp.ferc.global_limiter import throttle_ferc
from ferc_elibrary_mcp.ferc.rate_limit import TokenBucketLimiter


class FercClient:
    """Low-level async HTTP client for FERC eLibrary API endpoints."""

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        *,
        rps: float | None = None,
        burst: int | None = None,
    ) -> None:
        self._http = http
        self._owns_http = http is None
        self._limiter = TokenBucketLimiter(
            rps if rps is not None else config.RATE_LIMIT_RPS,
            burst=burst if burst is not None else config.RATE_LIMIT_BURST,
        )

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=config.BASE_URL,
                headers={
                    "User-Agent": config.USER_AGENT,
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(
                    connect=config.CONNECT_TIMEOUT,
                    read=config.SEARCH_TIMEOUT,
                    write=config.CONNECT_TIMEOUT,
                    pool=config.CONNECT_TIMEOUT,
                ),
                follow_redirects=True,
            )
        return self._http

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        content: bytes | str | None = None,
        params: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        client = self._ensure_http()
        last_status: int | None = None
        for attempt in range(config.MAX_RETRIES):
            await throttle_ferc()
            await self._limiter.acquire()
            try:
                response = await client.request(
                    method,
                    path,
                    json=json,
                    content=content,
                    params=params,
                    timeout=timeout,
                )
            except httpx.HTTPError as exc:
                if attempt == config.MAX_RETRIES - 1:
                    raise ELibraryRequestError(f"eLibrary request failed: {exc}") from exc
                await asyncio.sleep(config.RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue

            if response.status_code == 429:
                retry_after = _parse_retry_after(response)
                raise RateLimitError(
                    "FERC eLibrary rate-limited this client (HTTP 429). "
                    "Back off before retrying.",
                    retry_after=retry_after,
                )

            if _looks_like_ban(response):
                raise RateLimitError(
                    "FERC eLibrary rejected the request (possible IP ban). "
                    "Stop and retry much later.",
                )

            if response.status_code not in config.RETRY_STATUS_CODES:
                return response

            last_status = response.status_code
            if attempt < config.MAX_RETRIES - 1:
                await asyncio.sleep(config.RETRY_BACKOFF_SECONDS * (attempt + 1))

        raise ELibraryRequestError(
            f"eLibrary returned HTTP {last_status} for {path} after "
            f"{config.MAX_RETRIES} attempts"
        )

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        content: bytes | str | None = None,
        params: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        response = await self._send(
            method, path, json=json, content=content, params=params, timeout=timeout
        )
        if response.status_code >= 400:
            detail = _ferc_error_detail(response)
            raise ELibraryRequestError(
                f"eLibrary returned HTTP {response.status_code} for {path}{detail}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ELibraryRequestError(
                f"eLibrary returned non-JSON for {path}: {response.text[:200]}"
            ) from exc

    async def request_bytes(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        content: bytes | str | None = None,
        params: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, str | None, dict[str, str]]:
        response = await self._send(
            method,
            path,
            json=json,
            content=content,
            params=params,
            timeout=timeout if timeout is not None else config.DOWNLOAD_TIMEOUT,
        )
        if response.status_code >= 400:
            detail = _ferc_error_detail(response)
            raise ELibraryRequestError(
                f"eLibrary returned HTTP {response.status_code} for {path}{detail}"
            )
        disposition = response.headers.get("content-disposition", "")
        headers = {k.lower(): v for k, v in response.headers.items()}
        return response.content, _filename_from_disposition(disposition), headers


def _parse_retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _looks_like_ban(response: httpx.Response) -> bool:
    if response.status_code in {403, 451}:
        text = response.text[:500].lower()
        return "ban" in text or "blocked" in text or "forbidden" in text
    return False


def _ferc_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        text = response.text[:200]
        return f": {text}" if text else ""
    if isinstance(data, dict) and data.get("errorMessage"):
        return f": {data['errorMessage']}"
    return ""


def _filename_from_disposition(disposition: str) -> str | None:
    import re
    from urllib.parse import unquote

    if not disposition:
        return None
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
    if not match:
        return None
    return unquote(match.group(1)).strip() or None
