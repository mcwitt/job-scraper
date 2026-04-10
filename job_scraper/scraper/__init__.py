import asyncio
import importlib.util
import json
import logging
import subprocess
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import dacite

from job_scraper.config import BoardEntry, Config, CustomCommand, CustomScript
from job_scraper.models import Job
from job_scraper.scraper.http import Http

type ScrapeFn = Callable[[Http], AsyncIterator[Job]]

logger = logging.getLogger(__name__)

def _load_board_factory(platform: str) -> Callable:
    try:
        mod = importlib.import_module(
            f"job_scraper.scraper.{platform}"
        )
    except ModuleNotFoundError:
        msg = f"unknown board platform: {platform}"
        raise ValueError(msg) from None
    return mod.scrape_board


def _board_scraper(
    platform: str, entry: BoardEntry
) -> ScrapeFn:
    factory = _load_board_factory(platform)
    extra = dict(entry.extra)
    if platform == "workday":
        return factory(
            entry.slug,
            extra.pop("instance"),
            extra.pop("site"),
            name=entry.name,
            **extra,
        )
    return factory(entry.slug, name=entry.name, **extra)


def _load_custom_script(
    script: CustomScript, config_dir: Path
) -> ScrapeFn:
    path = (config_dir / script.path).resolve()
    spec = importlib.util.spec_from_file_location(
        f"custom_scraper.{script.name}", path
    )
    if spec is None or spec.loader is None:
        msg = f"cannot load custom scraper: {path}"
        raise ImportError(msg)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, "scrape", None)
    if fn is None:
        msg = f"custom scraper {path} has no 'scrape' function"
        raise ImportError(msg)
    return fn


def _command_scraper(cmd: CustomCommand) -> ScrapeFn:
    async def scrape(http: Http) -> AsyncIterator[Job]:
        proc = await asyncio.create_subprocess_exec(
            *cmd.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error(
                "custom command %s failed: %s",
                cmd.name,
                stderr.decode(),
            )
            return
        for line in stdout.decode().splitlines():
            if line.strip():
                yield dacite.from_dict(
                    Job, json.loads(line)
                )

    return scrape


def load_scrapers(
    config: Config, config_dir: Path
) -> list[tuple[str, ScrapeFn, float | None]]:
    scrapers: list[tuple[str, ScrapeFn, float | None]] = []

    for platform, entries in config.boards.items():
        for entry in entries:
            fn = _board_scraper(platform, entry)
            scrapers.append((entry.slug, fn, entry.cache_ttl))

    for custom in config.custom:
        if isinstance(custom, CustomScript):
            fn = _load_custom_script(custom, config_dir)
        else:
            fn = _command_scraper(custom)
        scrapers.append((custom.name, fn, custom.cache_ttl))

    return sorted(scrapers, key=lambda t: t[0])
