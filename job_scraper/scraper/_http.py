import asyncio
from collections.abc import Callable
from typing import Any

import httpx

from job_scraper.scraper import GetFn


def make_get(
    client: httpx.AsyncClient,
    cache: tuple[
        Callable[[str], dict[str, Any] | None],
        Callable[[str, dict[str, Any]], None],
    ],
    semaphore: asyncio.Semaphore,
) -> GetFn:
    """Return an async closure that fetches URLs with caching and rate-limiting."""
    cache_get, cache_put = cache

    async def get(url: str) -> str:
        cached = cache_get(url)
        if cached is not None:
            return cached["body"]
        async with semaphore:
            resp = await client.get(url)
            resp.raise_for_status()
            body = resp.text
        cache_put(url, {"body": body})
        return body

    return get
