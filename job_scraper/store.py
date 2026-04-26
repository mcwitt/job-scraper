"""Persistent per-job store with retention.

Keeps every job we're willing to show, keyed by `Job.hash`. Carries
forward jobs that weren't observed this run, evicting only after the
retention window has elapsed.
"""

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

import dacite

from job_scraper.models import Job, to_dict

DEFAULT_RETAIN_FOR_SECONDS = 7 * 86400
DEFAULT_WARN_AFTER_SECONDS = 2 * 86400


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts).replace(tzinfo=UTC)


def load(path: Path) -> dict[str, Job]:
    """Load store from a JSONL file. Missing file → empty store."""
    store: dict[str, Job] = {}
    try:
        text = path.read_text()
    except FileNotFoundError:
        return store
    for line in text.splitlines():
        if not line.strip():
            continue
        job = dacite.from_dict(Job, json.loads(line))
        store[job.hash] = job
    return store


def save(path: Path, store: dict[str, Job]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for job in store.values():
            f.write(json.dumps(to_dict(job)) + "\n")


def upsert_and_evict(
    store: dict[str, Job],
    fresh: list[Job],
    now: datetime,
    retain_for_seconds: int,
) -> dict[str, Job]:
    """Upsert fresh jobs (last_seen_at = now), evict carried jobs
    past the retention window.

    A carried job is also evicted if a freshly-observed job has the
    same url — it represents the same posting under a new hash
    (e.g. the description was edited), so the new entry supersedes
    the old.
    """
    now_str = now.isoformat()
    new_store: dict[str, Job] = {}
    fresh_hashes: set[str] = set()
    fresh_urls: set[str] = set()
    for job in fresh:
        stamped = dataclasses.replace(job, last_seen_at=now_str)
        new_store[stamped.hash] = stamped
        fresh_hashes.add(stamped.hash)
        fresh_urls.add(stamped.url)
    for h, job in store.items():
        if h in fresh_hashes:
            continue
        if job.url in fresh_urls:
            continue
        age = (now - parse_iso(job.last_seen_at)).total_seconds()
        if age <= retain_for_seconds:
            new_store[h] = job
    return new_store


def is_stale(
    last_seen_at: str, now: datetime, warn_after_seconds: int
) -> bool:
    if not last_seen_at:
        return False
    age = (now - parse_iso(last_seen_at)).total_seconds()
    return age > warn_after_seconds
