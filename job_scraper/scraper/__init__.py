import importlib
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from job_scraper.models import Job
from job_scraper.scraper._http import Http

type ScrapeFn = Callable[[Http], AsyncIterator[Job]]


def discover() -> list[tuple[str, ScrapeFn, float | None]]:
    """Scan this directory for scraper modules.

    Returns (name, scrape, cache_ttl) triples.  A scraper module is any
    `.py` file in this directory whose name does not start with ``_``.
    Each must export an async ``scrape`` function and may optionally
    export a ``cache_ttl`` float (seconds).
    """
    pkg_dir = Path(__file__).parent
    scrapers: list[tuple[str, ScrapeFn, float | None]] = []
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
