import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any


def _load(path: Path, ttl: float) -> dict[str, dict[str, Any]]:
    """Load cache entries from a JSONL file, discarding expired/corrupt lines."""
    entries: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return entries
    now = time.time()
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = record.get("_key")
        ts = record.get("_ts", 0)
        if key is None:
            continue
        if ttl > 0 and (now - ts) > ttl:
            continue
        entries[key] = record
    return entries


def _compact(path: Path, entries: dict[str, dict[str, Any]]) -> None:
    """Rewrite the cache file with only current entries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for record in entries.values():
            f.write(json.dumps(record, separators=(",", ":")) + "\n")


@asynccontextmanager
async def open_cache(
    path: str | Path, ttl: float = 0
) -> AsyncIterator[
    tuple[Callable[[str], dict[str, Any] | None], Callable[[str, dict[str, Any]], None]]
]:
    """Open a JSONL cache, yielding (get, put) closures.

    Args:
        path: Path to the JSONL cache file.
        ttl: Time-to-live in seconds. 0 means entries never expire.
    """
    p = Path(path)
    entries = _load(p, ttl)
    dirty: list[dict[str, Any]] = []

    def get(key: str) -> dict[str, Any] | None:
        record = entries.get(key)
        if record is None:
            return None
        # Return a copy without internal metadata
        return {k: v for k, v in record.items() if not k.startswith("_")}

    def put(key: str, value: dict[str, Any]) -> None:
        record = {**value, "_key": key, "_ts": time.time()}
        entries[key] = record
        dirty.append(record)
        # Append immediately so partial runs are recoverable
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")

    try:
        yield get, put
    finally:
        if dirty:
            _compact(p, entries)
