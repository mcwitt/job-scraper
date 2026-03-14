import asyncio
import importlib
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx

from job_scraper.models import Job
from job_scraper.scraper._http import Http

type ScrapeFn = Callable[[Http], AsyncIterator[Job]]

logger = logging.getLogger(__name__)


def run(fn: ScrapeFn) -> None:
    """Run a single scraper with no caching, for debugging."""
    import json
    from dataclasses import asdict

    async def _main() -> None:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=30
        ) as client:
            http = Http(
                client=client,
                cache_get=lambda _: None,
                cache_put=lambda _k, _v: None,
                semaphore=asyncio.Semaphore(5),
            )
            async for job in fn(http):
                print(json.dumps(asdict(job)))

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())


def discover() -> list[tuple[str, ScrapeFn, float | None]]:
    """Discover scrapers from Python modules in this directory.

    Returns (name, scrape, cache_ttl) triples.  Each non-underscore
    ``.py`` file must export an async ``scrape`` function and may
    optionally export a ``cache_ttl`` float.
    """
    scrapers: list[tuple[str, ScrapeFn, float | None]] = []
    pkg_dir = Path(__file__).parent
    for p in sorted(pkg_dir.glob("*.py")):
        if p.name.startswith("_"):
            continue
        mod_name = p.stem
        mod = importlib.import_module(f"job_scraper.scraper.{mod_name}")
        fn = getattr(mod, "scrape", None)
        if fn is None:
            continue
        ttl: float | None = getattr(mod, "cache_ttl", None)
        scrapers.append((mod_name, fn, ttl))
    return scrapers
