import asyncio
import logging

import anthropic
from anthropic.types import OutputConfigParam, TextBlock

from job_scraper.cache import Cache

logger = logging.getLogger(__name__)


async def create(
    client: anthropic.AsyncAnthropic,
    model: str,
    cache: Cache,
    cache_key: str,
    semaphore: asyncio.Semaphore,
    *,
    system: str | list,
    messages: list,
    output_config: OutputConfigParam | None = None,
    max_tokens: int = 4096,
) -> str:
    """Call client.messages.create with cache-through.

    Returns the text content of the first TextBlock.
    Cache is checked before acquiring the semaphore,
    so cached results don't consume a concurrency slot.
    """
    cached = cache.get(cache_key)
    if cached is not None:
        return cached["text"]

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if output_config is not None:
        kwargs["output_config"] = output_config

    async with semaphore:
        response = await client.messages.create(**kwargs)

    text = next(
        b for b in response.content if isinstance(b, TextBlock)
    ).text
    cache.put(cache_key, {"text": text})
    return text
