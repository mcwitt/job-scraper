"""Scraper status tracking — records per-source run outcomes."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceStatus:
    """Status summary for a single scraper source."""

    last_run_at: str | None = None
    last_run_ok: bool | None = None
    last_run_error: str | None = None
    last_run_jobs: int | None = None
    last_success_at: str | None = None
    last_success_jobs: int | None = None


def load(path: Path) -> dict[str, SourceStatus]:
    """Load status dict from JSON file. Returns empty dict if missing."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {
        name: SourceStatus(**entry)
        for name, entry in data.items()
    }


def save(path: Path, statuses: dict[str, SourceStatus]) -> None:
    """Persist status dict to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {name: asdict(s) for name, s in statuses.items()}
    path.write_text(json.dumps(data, indent=2) + "\n")


def record_run(
    statuses: dict[str, SourceStatus],
    name: str,
    timestamp: str,
    ok: bool,
    job_count: int = 0,
    error: str | None = None,
) -> dict[str, SourceStatus]:
    """Record a scraper run outcome, returning updated statuses."""
    prev = statuses.get(name)
    if ok:
        status = SourceStatus(
            last_run_at=timestamp,
            last_run_ok=True,
            last_run_jobs=job_count,
            last_success_at=timestamp,
            last_success_jobs=job_count,
        )
    else:
        status = SourceStatus(
            last_run_at=timestamp,
            last_run_ok=False,
            last_run_error=error,
            last_run_jobs=0,
            last_success_at=prev.last_success_at if prev else None,
            last_success_jobs=(
                prev.last_success_jobs if prev else None
            ),
        )
    return {**statuses, name: status}
