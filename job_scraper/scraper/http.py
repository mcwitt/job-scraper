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

_RETRYABLE = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 2.0  # seconds


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
            return CachedResponse(
                body=cached["body"],
                fetched_at=cached.get("fetched_at")
                or datetime.now(UTC).isoformat(),
            )
        last_exc: Exception | None = None
        reason = ""
        for attempt in range(_MAX_ATTEMPTS):
            text: str | None = None
            async with self.semaphore:
                try:
                    resp = await make_request()
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    last_exc = exc
                    reason = str(exc.response.status_code)
                    if exc.response.status_code not in _RETRYABLE:
                        raise
                except httpx.TransportError as exc:
                    last_exc = exc
                    reason = type(exc).__name__
                else:
                    text = resp.text
            if text is not None:
                return self._put_success(cache_key, text)
            if attempt + 1 < _MAX_ATTEMPTS:
                delay = _BACKOFF_BASE * (2**attempt)
                delay += random.uniform(0, delay)  # noqa: S311
                logger.debug(
                    "%s %s failed (%s), retry %d/%d in %.1fs",
                    method,
                    url,
                    reason,
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    delay,
                )
                await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

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
