import asyncio
import json as json_mod
import logging
import random
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

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
class Http:
    """Async HTTP client with caching and rate-limiting."""

    client: httpx.AsyncClient
    cache_get: Callable[[str], dict[str, Any] | None]
    cache_put: Callable[[str, dict[str, Any]], None]
    semaphore: asyncio.Semaphore
    cache_ttl: float | None = None

    def _put(
        self, key: str, value: dict[str, Any]
    ) -> str:
        now = datetime.now(UTC).isoformat()
        value = {**value, "fetched_at": now}
        if self.cache_ttl is not None:
            value["_ttl"] = self.cache_ttl
        self.cache_put(key, value)
        return now

    async def _fetch(
        self,
        cache_key: str,
        make_request: Callable[[], Coroutine[Any, Any, httpx.Response]],
        method: str,
        url: str,
    ) -> tuple[str, str]:
        """Execute an HTTP request with caching and retries."""
        cached = self.cache_get(cache_key)
        if cached is not None:
            if "error" in cached:
                raise CachedHttpError(url, cached["error"])
            fetched_at = cached.get("fetched_at") or datetime.now(
                UTC
            ).isoformat()
            return cached["body"], fetched_at
        last_exc: httpx.HTTPStatusError | None = None
        for attempt in range(_MAX_ATTEMPTS):
            async with self.semaphore:
                try:
                    resp = await make_request()
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    last_exc = exc
                    if exc.response.status_code not in _RETRYABLE:
                        self.cache_put(
                            cache_key,
                            {
                                "error": exc.response.status_code,
                                "_ttl": _ERROR_TTL,
                            },
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
            self.cache_put(
                cache_key,
                {
                    "error": last_exc.response.status_code,
                    "_ttl": _ERROR_TTL,
                },
            )
            raise last_exc
        fetched_at = self._put(cache_key, {"body": body})
        return body, fetched_at

    async def get(self, url: str) -> tuple[str, str]:
        """Return (body, fetched_at) for a GET request."""
        return await self._fetch(
            url,
            lambda: self.client.get(url),
            "GET",
            url,
        )

    async def post(
        self, url: str, *, json: dict[str, Any]
    ) -> tuple[str, str]:
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
    ) -> tuple[str, str]:
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
    ) -> tuple[str, str]:
        return await self._fetch(
            f"POST {url} {cache_suffix}",
            lambda: self.client.post(url, **kwargs),
            "POST",
            url,
        )
