import asyncio
import json as json_mod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

_ERROR_TTL = 3600  # cache HTTP errors for 1 hour


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

    async def get(self, url: str) -> tuple[str, str]:
        """Return (body, fetched_at) for a GET request."""
        cached = self.cache_get(url)
        if cached is not None:
            if "error" in cached:
                raise CachedHttpError(url, cached["error"])
            fetched_at = cached.get("fetched_at") or datetime.now(
                UTC
            ).isoformat()
            return cached["body"], fetched_at
        async with self.semaphore:
            try:
                resp = await self.client.get(url)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self.cache_put(
                    url,
                    {"error": exc.response.status_code, "_ttl": _ERROR_TTL},
                )
                raise
            body = resp.text
        fetched_at = self._put(url, {"body": body})
        return body, fetched_at

    async def post(
        self, url: str, *, json: dict[str, Any]
    ) -> tuple[str, str]:
        """Return (body, fetched_at) for a POST request."""
        key = f"POST {url} {json_mod.dumps(json, sort_keys=True)}"
        cached = self.cache_get(key)
        if cached is not None:
            if "error" in cached:
                raise CachedHttpError(url, cached["error"])
            fetched_at = cached.get("fetched_at") or datetime.now(
                UTC
            ).isoformat()
            return cached["body"], fetched_at
        async with self.semaphore:
            try:
                resp = await self.client.post(url, json=json)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self.cache_put(
                    key,
                    {"error": exc.response.status_code, "_ttl": _ERROR_TTL},
                )
                raise
            body = resp.text
        fetched_at = self._put(key, {"body": body})
        return body, fetched_at
