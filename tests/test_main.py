"""Tests for extracted pure functions and I/O helpers in main.py."""

import json
from pathlib import Path

import dacite
import pytest

from job_scraper.main import (
    _collect_scored_jobs,
    _compute_agreement,
    _priority,
    _score_to_examples,
    _select_jobs,
    _unscored_jobs,
    _write_jsonl,
    _write_surrogate_output,
)
from job_scraper.models import (
    Fit,
    Interest,
    Job,
    Score,
    ScoredJob,
    to_dict,
)
from job_scraper.surrogate import Example, Metrics

# ── Fixtures ──────────────────────────────────────────────


def _job(
    hash: str = "abc",
    title: str = "Engineer",
    company: str = "Acme",
    **kw,
) -> Job:
    return Job(
        hash=hash,
        title=title,
        company=company,
        url="https://example.com",
        description="desc",
        source="test",
        **kw,
    )


def _interest(score: int = 80) -> Interest:
    return Interest(
        strengths_alignment="ok",
        growth_opportunities="ok",
        role_type_fit="ok",
        industry_alignment="ok",
        company_reputation="ok",
        compensation="ok",
        location="ok",
        summary="ok",
        score=score,
    )


def _fit(score: int = 70) -> Fit:
    return Fit(
        demonstrated_experience="ok",
        institutional_credibility="ok",
        depth_vs_adjacency="ok",
        career_trajectory="ok",
        minimum_qualifications="ok",
        seniority_alignment="ok",
        location_visa="ok",
        summary="ok",
        score=score,
    )


def _score(interest: int = 80, fit: int = 70) -> Score:
    return Score(interest=_interest(interest), fit=_fit(fit))


def _example(
    hash: str = "abc",
    interest: int = 80,
    fit: int = 70,
) -> Example:
    return Example(
        hash=hash,
        title="t",
        description="d",
        company="c",
        location=None,
        team=None,
        comp=None,
        interest_score=interest,
        fit_score=fit,
    )


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    cache = tmp_path / "cache"
    cache.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    return cache, output


# ── _select_jobs ──────────────────────────────────────────


def test_dedup_only():
    jobs = [_job(hash="a"), _job(hash="a"), _job(hash="b")]
    universe, forced = _select_jobs(jobs, ("hash",), None, None)
    assert [j.hash for j in universe] == ["a", "b"]
    assert forced == []


def test_no_dedup_fields_passes_through():
    jobs = [_job(hash="a"), _job(hash="a")]
    universe, _ = _select_jobs(jobs, (), None, None)
    assert len(universe) == 2


def test_keyword_filter():
    jobs = [
        _job(hash="a", title="Python Engineer"),
        _job(hash="b", title="Java Developer"),
    ]
    universe, forced = _select_jobs(jobs, (), "python", None)
    assert [j.hash for j in universe] == ["a"]
    assert forced == []


def test_dedup_then_filter():
    jobs = [
        _job(hash="a", title="Python Engineer"),
        _job(hash="a", title="Python Engineer"),
        _job(hash="b", title="Java Developer"),
    ]
    universe, _ = _select_jobs(jobs, ("hash",), "python", None)
    assert len(universe) == 1


def test_select_jobs_empty():
    universe, forced = _select_jobs([], ("hash",), "python", None)
    assert universe == []
    assert forced == []


def test_multi_field_dedup():
    jobs = [
        _job(hash="a", title="Eng", company="X"),
        _job(hash="b", title="Eng", company="X"),
        _job(hash="c", title="Eng", company="Y"),
    ]
    universe, _ = _select_jobs(
        jobs, ("title", "company"), None, None
    )
    assert len(universe) == 2


def test_force_score_bypasses_keyword_filter():
    jobs = [
        _job(hash="a", title="Python Engineer", company="Acme"),
        _job(hash="b", title="Java Developer", company="Stripe"),
        _job(hash="c", title="Python Scientist", company="Stripe"),
    ]
    universe, forced = _select_jobs(
        jobs, (), "python", "company:stripe"
    )
    assert {j.hash for j in universe} == {"a", "b", "c"}
    assert {j.hash for j in forced} == {"b", "c"}


def test_force_score_overlap_with_keyword():
    jobs = [
        _job(hash="a", title="Python Engineer", company="Stripe"),
    ]
    universe, forced = _select_jobs(
        jobs, (), "python", "company:stripe"
    )
    assert [j.hash for j in universe] == ["a"]
    assert [j.hash for j in forced] == ["a"]


def test_force_score_only():
    jobs = [
        _job(hash="a", title="Eng", company="Acme"),
        _job(hash="b", title="Eng", company="Stripe"),
    ]
    universe, forced = _select_jobs(
        jobs, (), None, "company:stripe"
    )
    # No --keywords pre-filter: universe is full deduped set;
    # forced is the Stripe subset.
    assert {j.hash for j in universe} == {"a", "b"}
    assert {j.hash for j in forced} == {"b"}


# ── _unscored_jobs ────────────────────────────────────────


def test_unscored_filters_scored():
    jobs = [_job(hash="a"), _job(hash="b"), _job(hash="c")]
    result = _unscored_jobs(jobs, {"a", "c"})
    assert [j.hash for j in result] == ["b"]


def test_unscored_empty_scored():
    jobs = [_job(hash="a")]
    assert _unscored_jobs(jobs, set()) == jobs


def test_unscored_all_scored():
    jobs = [_job(hash="a")]
    assert _unscored_jobs(jobs, {"a"}) == []


# ── _score_to_examples ────────────────────────────────────


def test_score_to_examples_converts():
    jobs = [_job(hash="a"), _job(hash="b")]
    results = {
        "a": _score(interest=80, fit=70),
        "b": _score(interest=60, fit=50),
    }
    examples = _score_to_examples(jobs, results)
    assert len(examples) == 2
    assert examples[0].interest_score == 80
    assert examples[0].fit_score == 70
    assert examples[1].interest_score == 60


def test_score_to_examples_skips_missing():
    jobs = [_job(hash="a"), _job(hash="missing")]
    results = {"a": _score()}
    examples = _score_to_examples(jobs, results)
    assert len(examples) == 1
    assert examples[0].hash == "a"


def test_score_to_examples_empty():
    assert _score_to_examples([], {}) == []


def test_score_to_examples_returns_example_type():
    jobs = [_job(hash="a")]
    results = {"a": _score()}
    examples = _score_to_examples(jobs, results)
    assert isinstance(examples[0], Example)


# ── _priority ─────────────────────────────────────────────


def test_priority_computation():
    sj = ScoredJob(
        **to_dict(_job()),
        score_interest=_interest(80),
        score_fit=_fit(50),
    )
    assert _priority(sj) == pytest.approx(0.4)


def test_priority_zero():
    sj = ScoredJob(
        **to_dict(_job()),
        score_interest=_interest(0),
        score_fit=_fit(100),
    )
    assert _priority(sj) == 0.0


def test_priority_perfect():
    sj = ScoredJob(
        **to_dict(_job()),
        score_interest=_interest(100),
        score_fit=_fit(100),
    )
    assert _priority(sj) == pytest.approx(1.0)


# ── _collect_scored_jobs ──────────────────────────────────


def test_collect_builds_and_sorts():
    jobs = [_job(hash="a"), _job(hash="b")]
    results = {
        "a": _score(interest=50, fit=50),
        "b": _score(interest=90, fit=90),
    }
    scored = _collect_scored_jobs(jobs, results)
    assert len(scored) == 2
    assert scored[0].hash == "b"
    assert scored[1].hash == "a"


def test_collect_skips_missing():
    jobs = [_job(hash="a"), _job(hash="missing")]
    results = {"a": _score()}
    scored = _collect_scored_jobs(jobs, results)
    assert len(scored) == 1
    assert scored[0].hash == "a"


def test_collect_empty():
    assert _collect_scored_jobs([], {}) == []


def test_collect_preserves_fields():
    job = _job(
        hash="x",
        title="Senior Eng",
        company="BigCo",
        location="NYC",
    )
    results = {"x": _score(interest=80, fit=70)}
    scored = _collect_scored_jobs([job], results)
    assert scored[0].title == "Senior Eng"
    assert scored[0].company == "BigCo"
    assert scored[0].location == "NYC"
    assert scored[0].score_interest.score == 80
    assert scored[0].score_fit.score == 70


def test_collect_preserves_compensation_dataclass():
    from job_scraper.comp import format_compensation
    from job_scraper.models import Compensation

    comp = Compensation(100000, 150000, "USD", "annual", equity=True)
    job = _job(hash="x", compensation=comp)
    results = {"x": _score()}
    scored = _collect_scored_jobs([job], results)
    assert isinstance(scored[0].compensation, Compensation)
    assert format_compensation(scored[0].compensation) == (
        "$100k\u2013$150k/yr (+equity)"
    )


# ── _compute_agreement ────────────────────────────────────


def test_agreement_perfect():
    examples = [
        _example("a", interest=100, fit=100),
        _example("b", interest=50, fit=50),
        _example("c", interest=10, fit=10),
    ]
    surrogate = {"a": 1.0, "b": 0.25, "c": 0.01}
    agreement, n = _compute_agreement(examples, surrogate)
    assert n == 3
    assert agreement is not None
    assert agreement == pytest.approx(1.0)


def test_agreement_skips_missing():
    examples = [_example("a"), _example("missing")]
    surrogate = {"a": 0.5}
    _, n = _compute_agreement(examples, surrogate)
    assert n == 1


def test_agreement_too_few_returns_none():
    examples = [_example("a")]
    surrogate = {"a": 0.5}
    agreement, _ = _compute_agreement(examples, surrogate)
    assert agreement is None


def test_agreement_empty():
    agreement, n = _compute_agreement([], {})
    assert agreement is None
    assert n == 0


# ── _write_jsonl ──────────────────────────────────────────


def test_write_jsonl_jobs(tmp_path: Path):
    jobs = [_job(hash="a"), _job(hash="b")]
    path = tmp_path / "out.jsonl"
    _write_jsonl(jobs, path)

    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["hash"] == "a"
    assert json.loads(lines[1])["hash"] == "b"


def test_write_jsonl_scored_jobs(tmp_path: Path):
    scored = [
        ScoredJob(
            **to_dict(_job(hash="x")),
            score_interest=_interest(80),
            score_fit=_fit(70),
        )
    ]
    path = tmp_path / "out.jsonl"
    _write_jsonl(scored, path)

    data = json.loads(path.read_text().strip())
    assert data["hash"] == "x"
    assert data["score_interest"]["score"] == 80


def test_write_jsonl_empty(tmp_path: Path):
    path = tmp_path / "out.jsonl"
    _write_jsonl([], path)
    assert path.read_text() == ""


def test_write_jsonl_roundtrip(tmp_path: Path):
    """Written JSONL can be loaded back via dacite."""
    original = [
        _job(hash="a", location="SF", team="Infra"),
        _job(hash="b"),
    ]
    path = tmp_path / "out.jsonl"
    _write_jsonl(original, path)

    loaded = [
        dacite.from_dict(Job, json.loads(line))
        for line in path.read_text().strip().split("\n")
    ]
    assert loaded == original


# ── _write_surrogate_output ───────────────────────────────


def test_surrogate_output_writes_files(
    dirs: tuple[Path, Path],
):
    cache_dir, output_dir = dirs
    jobs = [_job(hash="a"), _job(hash="b")]
    scores = [0.75, 0.25]
    examples = [_example("a"), _example("b")]
    cv = Metrics(
        n_examples=2, cv_r2=0.5, cv_mae=0.1, cv_spearman=0.6
    )

    _write_surrogate_output(
        jobs, scores, examples, cv, cache_dir, output_dir
    )

    # Surrogate JSONL
    surr_path = output_dir / "jobs_surrogate.jsonl"
    lines = surr_path.read_text().strip().split("\n")
    assert len(lines) == 2
    d0 = json.loads(lines[0])
    assert d0["hash"] == "a"
    assert d0["surrogate_score"] == 0.75

    # Metrics
    metrics_path = cache_dir / "surrogate_metrics.jsonl"
    m = json.loads(metrics_path.read_text().strip())
    assert m["cv_r2"] == 0.5
    assert m["agreement_n"] == 2
    assert "timestamp" in m


def test_surrogate_output_metrics_appends(
    dirs: tuple[Path, Path],
):
    cache_dir, output_dir = dirs
    jobs = [_job(hash="a")]
    examples = [_example("a")]
    cv = Metrics(
        n_examples=1, cv_r2=0.0, cv_mae=0.0, cv_spearman=0.0
    )

    _write_surrogate_output(
        jobs, [0.5], examples, cv, cache_dir, output_dir
    )
    _write_surrogate_output(
        jobs, [0.6], examples, cv, cache_dir, output_dir
    )

    metrics_path = cache_dir / "surrogate_metrics.jsonl"
    lines = metrics_path.read_text().strip().split("\n")
    assert len(lines) == 2
