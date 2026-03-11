import importlib
import logging
import tomllib
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from job_scraper.models import Job
from job_scraper.scraper._http import Http

type ScrapeFn = Callable[[Http], AsyncIterator[Job]]

logger = logging.getLogger(__name__)

# Maps ATS type name to (module_path, factory_name)
_FACTORIES: dict[str, str] = {
    "greenhouse": "job_scraper.scraper._greenhouse",
    "ashby": "job_scraper.scraper._ashby",
    "lever": "job_scraper.scraper._lever",
    "gem": "job_scraper.scraper._gem",
    "workable": "job_scraper.scraper._workable",
    "workday": "job_scraper.scraper._workday",
}


def _load_boards(
    path: Path,
) -> list[tuple[str, ScrapeFn, float | None]]:
    """Load board-based scrapers from a TOML config file."""
    try:
        with path.open("rb") as f:
            config = tomllib.load(f)
    except FileNotFoundError:
        return []

    scrapers: list[tuple[str, ScrapeFn, float | None]] = []
    for ats, mod_path in _FACTORIES.items():
        boards = config.get(ats, [])
        if not boards:
            continue
        mod = importlib.import_module(mod_path)
        factory = mod.scrape_board
        for board in boards:
            name = board["name"]
            bid = board["board"]
            ttl: float | None = board.get("cache_ttl")
            if ats == "workday":
                fn = factory(
                    bid, board["instance"], board["site"], name=name
                )
            else:
                fn = factory(bid, name=name)
            slug = name.lower().replace(" ", "_")
            scrapers.append((f"{ats}_{slug}", fn, ttl))

    return scrapers


def discover(
    boards_path: Path = Path("boards.toml"),
) -> list[tuple[str, ScrapeFn, float | None]]:
    """Discover scrapers from boards.toml and module files.

    Returns (name, scrape, cache_ttl) triples. Sources:
    1. Board entries from boards.toml (ATS factories).
    2. Python modules in this directory whose name does not
       start with ``_``. Each must export an async ``scrape``
       function and may optionally export a ``cache_ttl`` float.
    """
    scrapers = _load_boards(boards_path)
    if scrapers:
        logger.info("loaded boards count=%d", len(scrapers))

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
