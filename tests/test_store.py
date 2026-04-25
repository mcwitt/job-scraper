"""Tests for job_scraper.store: upsert/evict, is_stale, roundtrip."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from job_scraper import store
from job_scraper.models import Job

NOW = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)


def _job(hash: str, **kw) -> Job:
    return Job(
        hash=hash,
        title=kw.pop("title", f"Eng {hash}"),
        company=kw.pop("company", "Acme"),
        url=kw.pop("url", "https://example.com"),
        description=kw.pop("description", "d"),
        source=kw.pop("source", "test"),
        **kw,
    )


# ── upsert_and_evict ───────────────────────────────────────


def test_upsert_stamps_last_seen_at():
    fresh = [_job("a")]
    new_store = store.upsert_and_evict(
        {}, fresh, NOW, retain_for_seconds=86400
    )
    assert new_store["a"].last_seen_at == NOW.isoformat()


def test_upsert_advances_last_seen_at_on_revisit():
    earlier = (NOW - timedelta(days=1)).isoformat()
    prev = {"a": _job("a", last_seen_at=earlier)}
    fresh = [_job("a")]
    new_store = store.upsert_and_evict(
        prev, fresh, NOW, retain_for_seconds=86400
    )
    assert new_store["a"].last_seen_at == NOW.isoformat()


def test_carried_within_retention_kept():
    one_day_ago = (NOW - timedelta(days=1)).isoformat()
    prev = {"a": _job("a", last_seen_at=one_day_ago)}
    new_store = store.upsert_and_evict(
        prev, [], NOW, retain_for_seconds=7 * 86400
    )
    assert "a" in new_store
    assert new_store["a"].last_seen_at == one_day_ago


def test_carried_past_retention_evicted():
    eight_days_ago = (NOW - timedelta(days=8)).isoformat()
    prev = {"a": _job("a", last_seen_at=eight_days_ago)}
    new_store = store.upsert_and_evict(
        prev, [], NOW, retain_for_seconds=7 * 86400
    )
    assert new_store == {}


def test_retention_boundary_at_exactly_retain_for():
    """Job exactly at the retention threshold is kept (≤)."""
    exactly = (NOW - timedelta(seconds=86400)).isoformat()
    prev = {"a": _job("a", last_seen_at=exactly)}
    new_store = store.upsert_and_evict(
        prev, [], NOW, retain_for_seconds=86400
    )
    assert "a" in new_store


def test_retain_for_zero_evicts_all_carried():
    """retain_for=0 disables retention. Carried jobs always have
    age > 0 (stamped in a prior run), so any positive retain
    threshold above 0 keeps them."""
    one_second_ago = (NOW - timedelta(seconds=1)).isoformat()
    prev = {"a": _job("a", last_seen_at=one_second_ago)}
    new_store = store.upsert_and_evict(
        prev, [], NOW, retain_for_seconds=0
    )
    assert new_store == {}


def test_fresh_overwrites_carried():
    """Fresh job replaces stored fields, not just timestamp."""
    earlier = (NOW - timedelta(days=1)).isoformat()
    prev = {"a": _job("a", title="Old Title", last_seen_at=earlier)}
    fresh = [_job("a", title="New Title")]
    new_store = store.upsert_and_evict(
        prev, fresh, NOW, retain_for_seconds=86400
    )
    assert new_store["a"].title == "New Title"
    assert new_store["a"].last_seen_at == NOW.isoformat()


def test_fresh_first_in_returned_dict():
    """Insertion order: fresh jobs first, carried jobs after."""
    earlier = (NOW - timedelta(days=1)).isoformat()
    prev = {
        "carried1": _job("carried1", last_seen_at=earlier),
        "carried2": _job("carried2", last_seen_at=earlier),
    }
    fresh = [_job("fresh1"), _job("fresh2")]
    new_store = store.upsert_and_evict(
        prev, fresh, NOW, retain_for_seconds=7 * 86400
    )
    assert list(new_store.keys()) == [
        "fresh1",
        "fresh2",
        "carried1",
        "carried2",
    ]


def test_partial_failure_carries_missing_jobs():
    """A scraper returning a subset still preserves the rest."""
    yesterday = (NOW - timedelta(days=1)).isoformat()
    prev = {
        "a": _job("a", last_seen_at=yesterday),
        "b": _job("b", last_seen_at=yesterday),
        "c": _job("c", last_seen_at=yesterday),
    }
    # Scraper only emits 'a' this run — b and c should carry
    new_store = store.upsert_and_evict(
        prev, [_job("a")], NOW, retain_for_seconds=7 * 86400
    )
    assert set(new_store.keys()) == {"a", "b", "c"}
    assert new_store["a"].last_seen_at == NOW.isoformat()
    assert new_store["b"].last_seen_at == yesterday


# ── is_stale ───────────────────────────────────────────────


def test_is_stale_just_under_threshold_false():
    just_under = (
        NOW - timedelta(seconds=86400 - 1)
    ).isoformat()
    assert not store.is_stale(just_under, NOW, 86400)


def test_is_stale_just_over_threshold_true():
    just_over = (
        NOW - timedelta(seconds=86400 + 1)
    ).isoformat()
    assert store.is_stale(just_over, NOW, 86400)


def test_is_stale_at_threshold_false():
    """Boundary: age == warn_after_seconds is not stale (strict >)."""
    exactly = (NOW - timedelta(seconds=86400)).isoformat()
    assert not store.is_stale(exactly, NOW, 86400)


def test_is_stale_empty_string_false():
    """Defensive: jobs without a stamped timestamp aren't stale."""
    assert not store.is_stale("", NOW, 86400)


# ── load / save roundtrip ─────────────────────────────────


def test_load_missing_file_returns_empty(tmp_path: Path):
    assert store.load(tmp_path / "nope.jsonl") == {}


def test_save_then_load_roundtrip(tmp_path: Path):
    path = tmp_path / "store.jsonl"
    original = {
        "a": _job("a", last_seen_at=NOW.isoformat()),
        "b": _job("b", last_seen_at=NOW.isoformat(), team="X"),
    }
    store.save(path, original)
    loaded = store.load(path)
    assert loaded == original


def test_save_creates_parent_dir(tmp_path: Path):
    path = tmp_path / "nested" / "dir" / "store.jsonl"
    store.save(path, {"a": _job("a", last_seen_at=NOW.isoformat())})
    assert path.exists()


# ── dedup ordering preserves fresh-this-run ───────────────


def test_dedup_after_upsert_prefers_fresh():
    """When fresh and carried collide on dedup key (different
    hashes, same title/company), fresh wins because _dedup keeps
    the first occurrence and upsert_and_evict puts fresh first."""
    from job_scraper.main import _select_jobs

    yesterday = (NOW - timedelta(days=1)).isoformat()
    prev = {
        "old_hash": _job(
            "old_hash",
            title="Senior Engineer",
            company="Acme",
            description="old desc",
            last_seen_at=yesterday,
        )
    }
    fresh = [
        _job(
            "new_hash",
            title="Senior Engineer",
            company="Acme",
            description="new desc",
        )
    ]
    new_store = store.upsert_and_evict(
        prev, fresh, NOW, retain_for_seconds=7 * 86400
    )
    deduped, _ = _select_jobs(
        list(new_store.values()),
        ("title", "company"),
        None,
        None,
    )
    assert len(deduped) == 1
    assert deduped[0].hash == "new_hash"
    assert deduped[0].description == "new desc"
