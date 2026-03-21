import asyncio
import json as json_mod
import logging
import random
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from job_scraper.cache import Cache

logger = logging.getLogger(__name__)

_ERROR_TTL = 3600  # cache HTTP errors for 1 hour
_RETRYABLE = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 2.0  # seconds


class CachedHttpError(Exception):
    """Raised when a cached error entry is hit."""

    def __init__(self, url: str, status: int):
        self.url = url
        self.status = status
        super().__init__(f"cached HTTP {status} for {url}")


@dataclass(frozen=True)
class CachedResponse:
    body: str
    fetched_at: str


@dataclass(frozen=True)
class Http:
    """Async HTTP client with caching and rate-limiting."""

    client: httpx.AsyncClient
    cache: Cache
    semaphore: asyncio.Semaphore
    cache_ttl: float | None = None

    def _put_success(
        self, key: str, body: str
    ) -> CachedResponse:
        now = datetime.now(UTC).isoformat()
        entry: dict[str, Any] = {
            "body": body,
            "fetched_at": now,
        }
        if self.cache_ttl is not None:
            entry["_ttl"] = self.cache_ttl
        self.cache.put(key, entry)
        return CachedResponse(body, now)

    def _put_error(self, key: str, status_code: int) -> None:
        self.cache.put(
            key, {"error": status_code, "_ttl": _ERROR_TTL}
        )

    async def _fetch(
        self,
        cache_key: str,
        make_request: Callable[[], Coroutine[Any, Any, httpx.Response]],
        method: str,
        url: str,
    ) -> CachedResponse:
        """Execute an HTTP request with caching and retries."""
        cached = self.cache.get(cache_key)
        if cached is not None:
            if "error" in cached:
                raise CachedHttpError(url, cached["error"])
            return CachedResponse(
                body=cached["body"],
                fetched_at=cached.get("fetched_at")
                or datetime.now(UTC).isoformat(),
            )
        last_exc: httpx.HTTPStatusError | None = None
        for attempt in range(_MAX_ATTEMPTS):
            async with self.semaphore:
                try:
                    resp = await make_request()
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    last_exc = exc
                    if exc.response.status_code not in _RETRYABLE:
                        self._put_error(
                            cache_key,
                            exc.response.status_code,
                        )
                        raise
                else:
                    body = resp.text
                    break
            # Retryable error — sleep outside semaphore
            delay = _BACKOFF_BASE * (2**attempt)
            delay += random.uniform(0, delay)  # noqa: S311
            logger.debug(
                "%s %s returned %d, retry %d/%d in %.1fs",
                method,
                url,
                last_exc.response.status_code,
                attempt + 1,
                _MAX_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)
        else:
            if last_exc is None:
                raise RuntimeError("unreachable")
            self._put_error(
                cache_key, last_exc.response.status_code
            )
            raise last_exc
        return self._put_success(cache_key, body)

    async def get(self, url: str) -> CachedResponse:
        """Return (body, fetched_at) for a GET request."""
        return await self._fetch(
            url,
            lambda: self.client.get(url),
            "GET",
            url,
        )

    async def post(
        self, url: str, *, json: dict[str, Any]
    ) -> CachedResponse:
        """Return (body, fetched_at) for a POST request."""
        return await self._post(
            url,
            json_mod.dumps(json, sort_keys=True),
            json=json,
        )

    async def post_form(
        self,
        url: str,
        *,
        data: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> CachedResponse:
        """Return (body, fetched_at) for a form-encoded POST."""
        return await self._post(
            url,
            json_mod.dumps(data, sort_keys=True),
            data=data,
            headers=headers,
        )

    async def _post(
        self,
        url: str,
        cache_suffix: str,
        **kwargs: Any,
    ) -> CachedResponse:
        return await self._fetch(
            f"POST {url} {cache_suffix}",
            lambda: self.client.post(url, **kwargs),
            "POST",
            url,
        )
