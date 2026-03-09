import importlib
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from job_scraper.models import Job
from job_scraper.scraper.http import Http

type ScrapeFn = Callable[[Http], AsyncIterator[Job]]


def discover() -> list[tuple[str, ScrapeFn]]:
    """Scan this directory for scraper modules and return (name, scrape) pairs.

    A scraper module is any `.py` file in this directory whose name does
    not start with ``_``.  Each must export an async ``scrape`` function.
    """
    pkg_dir = Path(__file__).parent
    scrapers: list[tuple[str, ScrapeFn]] = []
    for p in sorted(pkg_dir.glob("*.py")):
        if p.name.startswith("_"):
            continue
        mod_name = p.stem
        mod = importlib.import_module(f"job_scraper.scraper.{mod_name}")
        fn = getattr(mod, "scrape", None)
        if fn is None:
            continue
        scrapers.append((mod_name, fn))
    return scrapers
