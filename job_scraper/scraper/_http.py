import asyncio
import json as json_mod
from collections.abc import Callable
from dataclasses import dataclass
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

    def _put(self, key: str, value: dict[str, Any]) -> None:
        if self.cache_ttl is not None:
            value = {**value, "_ttl": self.cache_ttl}
        self.cache_put(key, value)

    async def get(self, url: str) -> str:
        cached = self.cache_get(url)
        if cached is not None:
            if "error" in cached:
                raise CachedHttpError(url, cached["error"])
            return cached["body"]
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
        self._put(url, {"body": body})
        return body

    async def post(
        self, url: str, *, json: dict[str, Any]
    ) -> str:
        key = f"POST {url} {json_mod.dumps(json, sort_keys=True)}"
        cached = self.cache_get(key)
        if cached is not None:
            if "error" in cached:
                raise CachedHttpError(url, cached["error"])
            return cached["body"]
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
        self._put(key, {"body": body})
        return body
