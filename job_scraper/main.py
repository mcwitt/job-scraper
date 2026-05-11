import asyncio
import dataclasses
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    import anthropic

    from job_scraper.models import Score, ScoredJob
    from job_scraper.status import SourceStatus
    from job_scraper.surrogate import Example, Metrics

import dacite
import httpx
import typer

from job_scraper import store
from job_scraper.cache import open_cache
from job_scraper.config import Config, load_config
from job_scraper.models import Job, to_dict
from job_scraper.relevance import filter_relevant
from job_scraper.scraper import ScrapeFn, load_scrapers
from job_scraper.scraper.http import Http

logger = logging.getLogger("job_scraper.main")
app = typer.Typer()


# Force Typer to keep `run` as a named subcommand even though
# it's the only one (otherwise Typer collapses to a root-only CLI).
@app.callback()
def _cli() -> None:
    """Job scraper: scrape and score job postings."""


def _load_jobs(path: Path) -> list[Job]:
    """Load Job objects from a JSONL file."""
    jobs: list[Job] = []
    with path.open() as f:
        for line in f:
            jobs.append(dacite.from_dict(Job, json.loads(line)))
    logger.info("loaded jobs count=%d path=%s", len(jobs), path)
    return jobs


def _dedup(
    jobs: list[Job], fields: tuple[str, ...]
) -> list[Job]:
    if not fields:
        return jobs
    seen: set[tuple[str | None, ...]] = set()
    result: list[Job] = []
    for job in jobs:
        key = tuple(getattr(job, f) for f in fields)
        if key not in seen:
            seen.add(key)
            result.append(job)
    logger.info(
        "dedup total=%d unique=%d fields=%s",
        len(jobs),
        len(result),
        ",".join(fields),
    )
    return result


async def _scrape(
    config: Config,
    config_dir: Path,
    cache_dir: Path,
    output_dir: Path,
    state_dir: Path,
    scrape_ttl: int,
    max_concurrent: int,
    retain_for_seconds: int,
    only: frozenset[str] | None = None,
    exclude: frozenset[str] | None = None,
) -> tuple[list[Job], dict[str, "SourceStatus"]]:
    from job_scraper import status as _status

    scrape_cache_path = cache_dir / "scrape.jsonl"
    status_path = cache_dir / "scraper_status.json"
    semaphore = asyncio.Semaphore(max_concurrent)

    scrapers = load_scrapers(config, config_dir)
    if only is not None:
        scrapers = [(n, f, t) for n, f, t in scrapers if n in only]
    if exclude is not None:
        scrapers = [(n, f, t) for n, f, t in scrapers if n not in exclude]
    if not scrapers:
        logger.warning("no scrapers found")
        return [], {}
    logger.info(
        "discovered scrapers count=%d names=%s",
        len(scrapers),
        ",".join(name for name, _, _ in scrapers),
    )

    statuses = _status.load(status_path)

    async with (
        httpx.AsyncClient(follow_redirects=True, timeout=30) as client,
        open_cache(scrape_cache_path, ttl=scrape_ttl) as scrape_cache,
    ):
        http = Http(client, scrape_cache, semaphore)

        all_jobs: list[Job] = []

        async def collect(
            name: str, fn: ScrapeFn, h: Http
        ) -> tuple[str, list[Job], str | None]:
            try:
                jobs: list[Job] = []
                async for job in fn(h):
                    jobs.append(job)
                logger.info("scraper=%s jobs=%d", name, len(jobs))
                return (name, jobs, None)
            except Exception as exc:
                logger.error("scraper=%s error=%s", name, exc)
                return (name, [], str(exc))

        results = await asyncio.gather(*(
            collect(
                name,
                fn,
                dataclasses.replace(http, cache_ttl=ttl)
                if ttl is not None
                else http,
            )
            for name, fn, ttl in scrapers
        ))

        errors: list[dict] = []
        now_str = datetime.now(UTC).isoformat()
        for name, jobs, error in results:
            all_jobs.extend(jobs)
            if error is None:
                statuses = _status.record_run(
                    statuses, name, now_str, ok=True, job_count=len(jobs)
                )
            else:
                statuses = _status.record_run(
                    statuses, name, now_str, ok=False, error=error
                )
                errors.append({
                    "scraper": name,
                    "timestamp": now_str,
                    "error": error,
                })

        if errors:
            errors_path = output_dir / "scraper_errors.jsonl"
            with errors_path.open("w") as f:
                for err in errors:
                    f.write(json.dumps(err) + "\n")
            logger.warning(
                "scrapers_failed=%d path=%s", len(errors), errors_path
            )

    _status.save(status_path, statuses)
    logger.info("saved scraper status path=%s", status_path)

    store_path = state_dir / "jobs_store.jsonl"
    prev = store.load(store_path)
    new_store = store.upsert_and_evict(
        prev, all_jobs, datetime.now(UTC), retain_for_seconds
    )
    store.save(store_path, new_store)
    fresh_count = len({j.hash for j in all_jobs})
    logger.info(
        "store path=%s fresh=%d carried=%d total=%d",
        store_path,
        fresh_count,
        len(new_store) - fresh_count,
        len(new_store),
    )

    return list(new_store.values()), statuses


def _select_jobs(
    jobs: list[Job],
    dedup_fields: tuple[str, ...],
    keywords: str | None,
    force_keywords: str | None,
) -> tuple[list[Job], list[Job]]:
    """Dedup and partition jobs (pure).

    Returns (universe, forced) where:
      - universe = (deduped & keywords) | forced. The output set;
        also the candidate pool for active learning + surrogate.
      - forced = deduped & force_keywords. LLM-scored
        unconditionally, bypassing the keywords pre-filter.
    """
    deduped = _dedup(jobs, dedup_fields)
    if keywords:
        filtered = filter_relevant(keywords, deduped)
        logger.info(
            "keyword filter total=%d matched=%d",
            len(deduped),
            len(filtered),
        )
    else:
        filtered = deduped
    if not force_keywords:
        return filtered, []
    forced = filter_relevant(force_keywords, deduped)
    logger.info("force-score filter matched=%d", len(forced))
    by_hash = {j.hash: j for j in filtered}
    for j in forced:
        by_hash.setdefault(j.hash, j)
    return list(by_hash.values()), forced


def _write_jsonl(
    data: list[Job] | list["ScoredJob"], path: Path
) -> None:
    with path.open("w") as f:
        for item in data:
            f.write(json.dumps(to_dict(item)) + "\n")
    logger.info("wrote count=%d path=%s", len(data), path)


def _unscored_jobs(
    jobs: list[Job], scored_hashes: set[str]
) -> list[Job]:
    return [j for j in jobs if j.hash not in scored_hashes]


def _score_to_examples(
    jobs: list[Job], results: dict[str, "Score"]
) -> list["Example"]:
    from job_scraper.surrogate import job_to_example

    return [
        job_to_example(
            job,
            results[job.hash].interest.score,
            results[job.hash].fit.score,
        )
        for job in jobs
        if job.hash in results
    ]


async def _init_scoring(
    preferences_path: Path,
    resume_path: Path,
    prep_model: str,
    cache_dir: Path,
    max_concurrent_api: int,
    companies_dir: Path,
) -> tuple["anthropic.AsyncAnthropic", str, str, dict[str, str]]:
    """Shared setup: client, prep artifacts, company context."""
    import anthropic

    from job_scraper.companies import load_companies

    preferences_text = preferences_path.read_text()
    if not preferences_text.strip():
        logger.warning(
            "preferences is empty path=%s", preferences_path
        )
    resume_text = resume_path.read_text()
    companies = load_companies(companies_dir)
    client = anthropic.AsyncAnthropic()

    interest_rubric, candidate_brief = await _generate_prep(
        preferences_text,
        resume_text,
        client,
        prep_model,
        cache_dir,
        max_concurrent_api,
    )
    return client, interest_rubric, candidate_brief, companies


def _generate_report(
    scored: list["ScoredJob"],
    output_dir: Path,
    linkedin_dir: Path,
    companies_dir: Path,
    warn_after_seconds: int,
) -> None:
    from job_scraper.linkedin import load as load_linkedin
    from job_scraper.report import render_report

    lookup = load_linkedin(linkedin_dir)
    report_path = output_dir / "report.html"
    render_report(
        scored,
        report_path,
        lookup=lookup,
        companies_dir=companies_dir,
        warn_after_seconds=warn_after_seconds,
    )
    logger.info("wrote report path=%s", report_path)


def _priority(j: "ScoredJob") -> float:
    return (j.score_interest.score / 100) * (
        j.score_fit.score / 100
    )


def _collect_scored_jobs(
    jobs: list[Job], results: dict[str, "Score"]
) -> list["ScoredJob"]:
    """Build sorted ScoredJob list from jobs and scores (pure)."""
    from job_scraper.models import ScoredJob

    scored = [
        ScoredJob(
            **{f.name: getattr(job, f.name) for f in dataclasses.fields(job)},
            score_interest=data.interest,
            score_fit=data.fit,
        )
        for job in jobs
        if (data := results.get(job.hash)) is not None
    ]
    return sorted(scored, key=_priority, reverse=True)


def _compute_agreement(
    examples: list["Example"],
    surrogate_by_hash: dict[str, float],
) -> tuple[float | None, int]:
    """Surrogate vs LLM rank agreement (pure)."""
    from job_scraper.surrogate import rank_agreement

    actual, predicted = [], []
    for ex in examples:
        if ex.hash in surrogate_by_hash:
            actual.append(
                (ex.interest_score / 100) * (ex.fit_score / 100)
            )
            predicted.append(surrogate_by_hash[ex.hash])
    return rank_agreement(actual, predicted), len(actual)


def _write_surrogate_output(
    filtered_jobs: list[Job],
    surrogate_scores: list[float],
    examples: list["Example"],
    cv_metrics: "Metrics",
    cache_dir: Path,
    output_dir: Path,
) -> None:
    """Write surrogate scores, compute agreement, persist metrics."""
    surrogate_path = output_dir / "jobs_surrogate.jsonl"
    surrogate_by_hash: dict[str, float] = {}
    with surrogate_path.open("w") as f:
        for job, s in zip(
            filtered_jobs, surrogate_scores, strict=True
        ):
            surrogate_by_hash[job.hash] = s
            d = to_dict(job)
            d["surrogate_score"] = round(s, 6)
            f.write(json.dumps(d) + "\n")
    logger.info(
        "wrote surrogate scores count=%d path=%s",
        len(filtered_jobs),
        surrogate_path,
    )
    agreement, n = _compute_agreement(
        examples, surrogate_by_hash
    )
    if agreement is not None:
        logger.info(
            "surrogate agreement n=%d spearman=%.4f",
            n,
            agreement,
        )

    # Metrics
    metrics_path = cache_dir / "surrogate_metrics.jsonl"
    metrics_record = {
        "timestamp": datetime.now(UTC).isoformat(),
        **to_dict(cv_metrics),
        "agreement_spearman": agreement,
        "agreement_n": n,
    }
    with metrics_path.open("a") as f:
        f.write(json.dumps(metrics_record) + "\n")
    logger.info("wrote surrogate metrics path=%s", metrics_path)


async def _generate_prep(
    preferences_text: str,
    resume_text: str,
    client: "anthropic.AsyncAnthropic",
    prep_model: str,
    cache_dir: Path,
    max_concurrent_api: int,
) -> tuple[str, str]:
    from job_scraper.prep import (
        generate_candidate_brief,
        generate_interest_rubric,
    )

    semaphore = asyncio.Semaphore(max_concurrent_api)
    async with open_cache(cache_dir / "prep.jsonl") as cache:
        return await asyncio.gather(
            generate_interest_rubric(
                preferences_text,
                client,
                prep_model,
                cache,
                semaphore,
            ),
            generate_candidate_brief(
                resume_text,
                client,
                prep_model,
                cache,
                semaphore,
            ),
        )


async def _run(
    config: Config,
    config_dir: Path,
    cache_dir: Path,
    output_dir: Path,
    state_dir: Path,
    scrape_ttl: int,
    retain_for_seconds: int,
    warn_after_seconds: int,
    model: str,
    prep_model: str,
    preferences_path: Path,
    max_concurrent: int,
    max_concurrent_api: int,
    skip_score: bool,
    report: bool,
    keywords: str | None,
    force_score_keywords: str | None,
    linkedin_dir: Path,
    dedup_fields: tuple[str, ...],
    resume_path: Path,
    companies_dir: Path,
    init_num_exploit: int,
    num_explore: int,
    num_exploit: int,
    init_learning_iters: int,
    learning_iters: int,
    scrape_only: bool = False,
    input_jobs: Path | None = None,
    status_report: bool = False,
    only: frozenset[str] | None = None,
    exclude: frozenset[str] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Obtain raw jobs ---
    if input_jobs is not None:
        raw_jobs = _load_jobs(input_jobs)
    else:
        raw_jobs, statuses = await _scrape(
            config,
            config_dir,
            cache_dir,
            output_dir,
            state_dir,
            scrape_ttl,
            max_concurrent,
            retain_for_seconds,
            only=only,
            exclude=exclude,
        )
        if status_report:
            from job_scraper.status_report import (
                render_status_report,
            )

            report_path = output_dir / "status.html"
            render_status_report(statuses, report_path)
            logger.info(
                "wrote status report path=%s", report_path
            )
        if scrape_only:
            return

    if not raw_jobs:
        logger.warning("no jobs to process")
        return

    filtered, forced = _select_jobs(
        raw_jobs, dedup_fields, keywords, force_score_keywords
    )

    if skip_score:
        _write_jsonl(filtered, output_dir / "jobs.jsonl")
        return

    from job_scraper.scorer import score_combined
    from job_scraper.surrogate import (
        TrainingData,
        predict,
        seed_by_similarity,
        select_by_disagreement,
        select_by_score,
        train,
    )

    client, interest_rubric, candidate_brief, companies = (
        await _init_scoring(
            preferences_path,
            resume_path,
            prep_model,
            cache_dir,
            max_concurrent_api,
            companies_dir,
        )
    )

    # --- Active learning ---
    training = TrainingData(
        cache_dir / "surrogate_training.jsonl",
        interest_rubric,
        candidate_brief,
    )
    cold_start = len(training.examples) == 0
    reference = (
        preferences_path.read_text()
        + "\n\n"
        + resume_path.read_text()
    )
    scored_before = len(training.scored_hashes)

    score_cache_path = cache_dir / "score.jsonl"
    async with open_cache(score_cache_path) as score_cache:

        async def _score(
            batch: list[Job],
        ) -> dict[str, "Score"]:
            return await score_combined(
                batch,
                interest_rubric,
                candidate_brief,
                companies,
                client,
                model,
                score_cache,
                max_concurrent=max_concurrent_api,
            )

        # Force-score upfront so they become training examples
        # before AL selection.
        if forced:
            unscored_forced = _unscored_jobs(
                forced, training.scored_hashes
            )
            if unscored_forced:
                logger.info(
                    "force-scoring count=%d", len(unscored_forced)
                )
                results = await _score(unscored_forced)
                training.append(
                    _score_to_examples(unscored_forced, results)
                )

        # Cold start: seed by similarity to user profile
        if cold_start:
            seed = seed_by_similarity(
                _unscored_jobs(
                    filtered, training.scored_hashes
                ),
                reference,
                init_num_exploit,
            )
            logger.info(
                "cold start: seeding with %d similar jobs",
                len(seed),
            )
            results = await _score(seed)
            training.append(_score_to_examples(seed, results))

        # Explore/exploit loop
        n_iters = (
            init_learning_iters if cold_start else learning_iters
        )
        for iteration in range(n_iters):
            unscored = _unscored_jobs(
                filtered, training.scored_hashes
            )
            if not unscored:
                logger.info(
                    "active learning: no unscored jobs remain"
                )
                break

            # Explore: high-disagreement examples
            vec, predictor, ensemble, metrics = train(
                training.examples
            )
            logger.info(
                "active learning iter=%d/%d "
                "unscored=%d r2=%.4f spearman=%.4f",
                iteration + 1,
                n_iters,
                len(unscored),
                metrics.cv_r2,
                metrics.cv_spearman,
            )
            explore = select_by_disagreement(
                vec,
                ensemble,
                unscored,
                min(num_explore, len(unscored)),
            )
            results = await _score(explore)
            training.append(
                _score_to_examples(explore, results)
            )

            # Exploit: highest-predicted examples
            unscored = _unscored_jobs(
                filtered, training.scored_hashes
            )
            if not unscored:
                break
            vec, predictor, ensemble, _ = train(
                training.examples
            )
            exploit = select_by_score(
                vec,
                predictor,
                unscored,
                min(num_exploit, len(unscored)),
            )
            results = await _score(exploit)
            training.append(
                _score_to_examples(exploit, results)
            )

        scored_during = (
            len(training.scored_hashes) - scored_before
        )
        logger.info(
            "learning complete: scored=%d total_training=%d",
            scored_during,
            len(training.examples),
        )

        # --- Surrogate output ---
        vectorizer, predictor, _, cv_metrics = train(
            training.examples
        )
        surrogate_scores = predict(
            (vectorizer, predictor), filtered
        )
        _write_surrogate_output(
            filtered,
            surrogate_scores,
            training.examples,
            cv_metrics,
            cache_dir,
            output_dir,
        )

        # --- Collect full scores (cache hits) ---
        scored_jobs = [
            j
            for j in filtered
            if j.hash in training.scored_hashes
        ]
        all_results = await _score(scored_jobs)

    scored = _collect_scored_jobs(scored_jobs, all_results)
    _write_jsonl(scored, output_dir / "jobs.jsonl")

    if report:
        _generate_report(
            scored,
            output_dir,
            linkedin_dir,
            companies_dir,
            warn_after_seconds,
        )


@app.command()
def run(
    config: Annotated[
        Path, typer.Option(help="Path to scrape.toml config")
    ] = Path("scrape.toml"),
    cache_dir: Annotated[Path, typer.Option(help="Cache directory")] = Path(
        "data/cache"
    ),
    output_dir: Annotated[Path, typer.Option(help="Output directory")] = Path(
        "data/output"
    ),
    state_dir: Annotated[
        Path, typer.Option(help="Persistent state directory")
    ] = Path("data/state"),
    scrape_ttl: Annotated[
        int, typer.Option(help="Scrape cache TTL in seconds")
    ] = 86400,
    retain_for_seconds: Annotated[
        int,
        typer.Option(
            help="Carry forward unobserved jobs for this many"
            " seconds before evicting (0 disables retention)"
        ),
    ] = store.DEFAULT_RETAIN_FOR_SECONDS,
    warn_after_seconds: Annotated[
        int,
        typer.Option(
            help="Flag jobs as stale in the report if not"
            " observed within this many seconds"
        ),
    ] = store.DEFAULT_WARN_AFTER_SECONDS,
    model: Annotated[
        str, typer.Option(help="Claude model for scoring")
    ] = "claude-haiku-4-5",
    prep_model: Annotated[
        str,
        typer.Option(help="Claude model for prep generation"),
    ] = "claude-sonnet-4-6",
    preferences: Annotated[
        Path, typer.Option(help="Path to candidate preferences")
    ] = Path("preferences.md"),
    max_concurrent: Annotated[
        int, typer.Option(help="Max concurrent HTTP requests")
    ] = 20,
    max_concurrent_api: Annotated[
        int,
        typer.Option(help="Max concurrent Claude API requests"),
    ] = 10,
    skip_score: Annotated[
        bool, typer.Option("--skip-score", help="Skip scoring step")
    ] = False,
    report: Annotated[
        bool, typer.Option("--report", help="Generate HTML report")
    ] = False,
    keywords: Annotated[
        str | None,
        typer.Option(help="FTS5 expression for boolean pre-filtering"),
    ] = None,
    force_score_keywords: Annotated[
        str | None,
        typer.Option(
            help="FTS5 expression for jobs to LLM-score"
            " unconditionally, bypassing --keywords and the"
            " active-learning loop"
        ),
    ] = None,
    linkedin_dir: Annotated[
        Path, typer.Option(help="LinkedIn data directory")
    ] = Path("linkedin"),
    resume: Annotated[
        Path, typer.Option(help="Path to resume for recruiter scoring")
    ] = Path("resume.md"),
    companies_dir: Annotated[
        Path,
        typer.Option(help="Directory of company context .md files"),
    ] = Path("companies"),
    dedup_fields: Annotated[
        str,
        typer.Option(help="Comma-separated Job fields for dedup"),
    ] = "title,company,team,description",
    scrape_only: Annotated[
        bool,
        typer.Option(
            "--scrape-only",
            help="Scrape only, update the jobs store and exit",
        ),
    ] = False,
    input_jobs: Annotated[
        Path | None,
        typer.Option(
            help="Skip scrape, read raw jobs from JSONL file"
        ),
    ] = None,
    status_report: Annotated[
        bool,
        typer.Option(
            "--status-report",
            help="Generate scraper status HTML report",
        ),
    ] = False,
    only: Annotated[
        str | None,
        typer.Option(
            help="Comma-separated scraper names to run (default: all)"
        ),
    ] = None,
    exclude: Annotated[
        str | None,
        typer.Option(
            help="Comma-separated scraper names to exclude"
        ),
    ] = None,
    init_num_exploit: Annotated[
        int,
        typer.Option(
            help="Jobs to seed by similarity for cold start"
        ),
    ] = 100,
    num_explore: Annotated[
        int,
        typer.Option(
            help="Jobs to explore per active learning iteration"
        ),
    ] = 10,
    num_exploit: Annotated[
        int,
        typer.Option(
            help="Jobs to exploit per active learning iteration"
        ),
    ] = 10,
    init_learning_iters: Annotated[
        int,
        typer.Option(
            help="Active learning iterations during cold start"
        ),
    ] = 20,
    learning_iters: Annotated[
        int,
        typer.Option(
            help="Active learning iterations during warm start"
        ),
    ] = 1,
) -> None:
    """Scrape and score job postings."""
    if scrape_only and input_jobs is not None:
        raise typer.BadParameter(
            "--scrape-only and --input-jobs are mutually exclusive"
        )
    if only is not None and exclude is not None:
        raise typer.BadParameter(
            "--only and --exclude are mutually exclusive"
        )
    cfg = load_config(config)
    config_dir = config.resolve().parent
    only_set: frozenset[str] | None = (
        frozenset(s.strip() for s in only.split(","))
        if only
        else None
    )
    exclude_set: frozenset[str] | None = (
        frozenset(s.strip() for s in exclude.split(","))
        if exclude
        else None
    )
    names_to_check = only_set or exclude_set
    if names_to_check:
        valid = cfg.all_names()
        bad = sorted(names_to_check - valid)
        if bad:
            raise typer.BadParameter(
                f"Unknown scrapers: {', '.join(bad)}"
            )
    logging.basicConfig(level=logging.INFO)
    if dedup_fields:
        fields: tuple[str, ...] = tuple(
            f.strip() for f in dedup_fields.split(",")
        )
        job_attrs = {f.name for f in dataclasses.fields(Job)}
        bad_fields = [f for f in fields if f not in job_attrs]
        if bad_fields:
            raise typer.BadParameter(
                f"Unknown Job fields: {', '.join(bad_fields)}"
            )
    else:
        fields = ()
    asyncio.run(
        _run(
            config=cfg,
            config_dir=config_dir,
            cache_dir=cache_dir,
            output_dir=output_dir,
            state_dir=state_dir,
            scrape_ttl=scrape_ttl,
            retain_for_seconds=retain_for_seconds,
            warn_after_seconds=warn_after_seconds,
            model=model,
            prep_model=prep_model,
            preferences_path=preferences,
            max_concurrent=max_concurrent,
            max_concurrent_api=max_concurrent_api,
            skip_score=skip_score,
            report=report,
            keywords=keywords,
            force_score_keywords=force_score_keywords,
            linkedin_dir=linkedin_dir,
            dedup_fields=fields,
            resume_path=resume,
            companies_dir=companies_dir,
            scrape_only=scrape_only,
            input_jobs=input_jobs,
            status_report=status_report,
            only=only_set,
            exclude=exclude_set,
            init_num_exploit=init_num_exploit,
            num_explore=num_explore,
            num_exploit=num_exploit,
            init_learning_iters=init_learning_iters,
            learning_iters=learning_iters,
        )
    )


if __name__ == "__main__":
    app()
