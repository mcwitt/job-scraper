import importlib
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from job_scraper.models import Job
from job_scraper.scraper.http import Http

type ScrapeFn = Callable[[Http], AsyncIterator[Job]]

logger = logging.getLogger(__name__)


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
