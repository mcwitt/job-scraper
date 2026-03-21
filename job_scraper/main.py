import asyncio
import dataclasses
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from job_scraper.status import SourceStatus

import dacite
import httpx
import typer

from job_scraper.cache import open_cache
from job_scraper.models import Job, to_dict
from job_scraper.relevance import filter_relevant
from job_scraper.scraper import ScrapeFn, discover
from job_scraper.scraper.http import Http

logger = logging.getLogger("job_scraper.main")
app = typer.Typer()


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
    cache_dir: Path,
    output_dir: Path,
    scrape_ttl: int,
    max_concurrent: int,
    only: frozenset[str] | None = None,
    exclude: frozenset[str] | None = None,
) -> tuple[list[Job], dict[str, "SourceStatus"]]:
    """Run scrape phase: discover → scrape → write jobs_raw.jsonl."""
    from job_scraper import status as _status

    scrape_cache_path = cache_dir / "scrape.jsonl"
    status_path = cache_dir / "scraper_status.json"
    semaphore = asyncio.Semaphore(max_concurrent)

    scrapers = discover()
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

    raw_path = output_dir / "jobs_raw.jsonl"
    with raw_path.open("w") as f:
        for job in all_jobs:
            f.write(json.dumps(to_dict(job)) + "\n")
    logger.info("wrote raw jobs count=%d path=%s", len(all_jobs), raw_path)

    return all_jobs, statuses


async def _run(
    cache_dir: Path,
    output_dir: Path,
    scrape_ttl: int,
    model: str,
    rubric_model: str,
    preferences_path: Path,
    max_concurrent: int,
    max_concurrent_api: int,
    skip_score: bool,
    report: bool,
    keywords: str | None,
    top_k: int,
    linkedin_dir: Path,
    dedup_fields: tuple[str, ...],
    resume_path: Path,
    num_cold_start: int,
    num_explore: int,
    num_active_iters: int,
    scrape_only: bool = False,
    input_jobs: Path | None = None,
    status_report: bool = False,
    only: frozenset[str] | None = None,
    exclude: frozenset[str] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Obtain raw jobs ---
    if input_jobs is not None:
        all_jobs = _load_jobs(input_jobs)
    else:
        all_jobs, statuses = await _scrape(
            cache_dir, output_dir, scrape_ttl, max_concurrent,
            only=only, exclude=exclude,
        )
        if status_report:
            from job_scraper.status_report import render_status_report

            report_path = output_dir / "status.html"
            render_status_report(statuses, report_path)
            logger.info("wrote status report path=%s", report_path)
        if scrape_only:
            return

    if not all_jobs:
        logger.warning("no jobs to process")
        return

    output_path = output_dir / "jobs.jsonl"

    # --- Dedup (before filtering) ---
    all_jobs = _dedup(all_jobs, dedup_fields)

    # --- Keywords boolean filter ---
    if keywords:
        filtered_jobs = filter_relevant(keywords, all_jobs)
        logger.info(
            "keyword filter total=%d matched=%d",
            len(all_jobs),
            len(filtered_jobs),
        )
    else:
        filtered_jobs = all_jobs

    if skip_score:
        with output_path.open("w") as f:
            for job in filtered_jobs:
                f.write(json.dumps(to_dict(job)) + "\n")
        logger.info(
            "wrote jobs count=%d path=%s",
            len(filtered_jobs),
            output_path,
        )
        return

    # --- Surrogate-guided scoring ---
    import anthropic

    from job_scraper.companies import load_companies
    from job_scraper.models import Score, ScoredJob
    from job_scraper.surrogate import (
        append_examples,
        job_to_example,
        load_examples,
        predict,
        rank_agreement,
        seed_by_similarity,
        select_by_disagreement,
        train,
    )

    preferences_text = preferences_path.read_text()
    if not preferences_text.strip():
        logger.warning(
            "preferences is empty path=%s", preferences_path
        )

    resume_text = resume_path.read_text()
    companies = load_companies()
    ai = anthropic.AsyncAnthropic()

    # --- Generate rubrics ---
    from job_scraper.rubric import (
        generate_fit_rubrics,
        generate_interest_rubric,
    )
    from job_scraper.scorer import score_combined

    rubric_cache_path = cache_dir / "rubrics.jsonl"
    score_cache_path = cache_dir / "score.jsonl"

    company_names = {j.company for j in filtered_jobs}
    rubric_semaphore = asyncio.Semaphore(max_concurrent_api)

    async with open_cache(rubric_cache_path) as rubric_cache:
        interest_rubric, fit_rubrics = await asyncio.gather(
            generate_interest_rubric(
                preferences_text,
                ai,
                rubric_model,
                rubric_cache,
                rubric_semaphore,
            ),
            generate_fit_rubrics(
                resume_text,
                companies,
                company_names,
                ai,
                rubric_model,
                rubric_cache,
                rubric_semaphore,
            ),
        )

    def _to_examples(
        jobs: list[Job],
        results: dict[str, Score],
        exclude: set[str] | None = None,
    ) -> list:
        return [
            job_to_example(
                job,
                results[job.hash].interest.score,
                results[job.hash].fit.score,
            )
            for job in jobs
            if job.hash in results
            and (exclude is None or job.hash not in exclude)
        ]

    training_path = cache_dir / "surrogate_training.jsonl"
    examples = load_examples(training_path)
    scored_hashes = {ex.hash for ex in examples}

    # Partition into already-scored and unscored
    unscored = [
        j for j in filtered_jobs if j.hash not in scored_hashes
    ]
    cold_start = len(examples) == 0

    reference = preferences_text + "\n\n" + resume_text

    async with open_cache(score_cache_path) as score_cache:

        async def _score_and_learn(
            batch: list[Job],
        ) -> None:
            nonlocal examples
            if not batch:
                return
            results = await score_combined(
                batch,
                interest_rubric,
                fit_rubrics,
                ai,
                model,
                score_cache,
                max_concurrent=max_concurrent_api,
            )
            new_examples = _to_examples(
                batch, results
            )
            append_examples(training_path, new_examples)
            examples = examples + new_examples
            scored_hashes.update(
                ex.hash for ex in new_examples
            )
            logger.info(
                "scored=%d total_training=%d",
                len(new_examples),
                len(examples),
            )

        scored_before = len(scored_hashes)

        if cold_start:
            # Phase 1: Seed by similarity
            seed_sample = seed_by_similarity(
                unscored, reference, num_cold_start
            )
            logger.info(
                "cold start: selected %d most similar jobs",
                len(seed_sample),
            )
            await _score_and_learn(seed_sample)

            # Phase 2: Active learning loop
            for iteration in range(num_active_iters):
                unscored = [
                    j
                    for j in filtered_jobs
                    if j.hash not in scored_hashes
                ]
                if not unscored:
                    logger.info(
                        "active learning: no unscored jobs remain"
                    )
                    break
                logger.info(
                    "active learning: unscored=%d",
                    len(unscored),
                )
                vec, _, ensemble, iter_metrics = train(
                    examples
                )
                logger.info(
                    "active learning iter=%d r2=%.4f"
                    " spearman=%.4f",
                    iteration + 1,
                    iter_metrics.cv_r2,
                    iter_metrics.cv_spearman,
                )
                explore = select_by_disagreement(
                    vec,
                    ensemble,
                    unscored,
                    min(num_explore, len(unscored)),
                )
                await _score_and_learn(explore)
        else:
            # Warm start: disagreement-based exploration
            logger.info(
                "warm start: unscored=%d", len(unscored)
            )
            vec, _, ensemble, warm_metrics = train(examples)
            logger.info(
                "warm start: r2=%.4f spearman=%.4f",
                warm_metrics.cv_r2,
                warm_metrics.cv_spearman,
            )
            explore = select_by_disagreement(
                vec,
                ensemble,
                unscored,
                min(num_explore, len(unscored)),
            )
            await _score_and_learn(explore)

        scored_during = len(scored_hashes) - scored_before
        logger.info(
            "exploration complete: scored=%d total_training=%d",
            scored_during,
            len(examples),
        )

        # Final train + rank
        vectorizer, ridge, _, cv_metrics = train(examples)

        surrogate_scores = predict(
            (vectorizer, ridge), filtered_jobs
        )
        surrogate_path = output_dir / "jobs_surrogate.jsonl"
        with surrogate_path.open("w") as f:
            for job, s in zip(
                filtered_jobs, surrogate_scores, strict=True
            ):
                d = to_dict(job)
                d["surrogate_score"] = round(s, 6)
                f.write(json.dumps(d) + "\n")
        logger.info(
            "wrote surrogate scores count=%d path=%s",
            len(filtered_jobs),
            surrogate_path,
        )

        # Rank and take top-k
        ranked = sorted(
            zip(filtered_jobs, surrogate_scores, strict=True),
            key=lambda x: x[1],
            reverse=True,
        )
        top_jobs = [j for j, _ in ranked[:top_k]]
        if ranked:
            logger.info(
                "surrogate top_k=%d best=%.4f cutoff=%.4f",
                top_k,
                ranked[0][1],
                ranked[min(top_k, len(ranked)) - 1][1],
            )

        # Score top-k (cache hits for already-scored explore jobs)
        topk_results = await score_combined(
            top_jobs,
            interest_rubric,
            fit_rubrics,
            ai,
            model,
            score_cache,
            max_concurrent=max_concurrent_api,
        )

        # Append newly scored (not in explore batch) to training data
        newly_scored = _to_examples(
            top_jobs, topk_results, exclude=scored_hashes
        )
        if newly_scored:
            append_examples(training_path, newly_scored)
            logger.info(
                "appended top-k training examples count=%d",
                len(newly_scored),
            )

        # Top-k agreement: surrogate vs LLM on scored top-k jobs
        surr_by_hash = {
            j.hash: s
            for j, s in zip(
                filtered_jobs, surrogate_scores, strict=True
            )
        }
        topk_surr, topk_actual = [], []
        for job in top_jobs:
            if data := topk_results.get(job.hash):
                topk_surr.append(surr_by_hash[job.hash])
                topk_actual.append(
                    (data.interest.score / 100)
                    * (data.fit.score / 100)
                )

        topk_spearman = rank_agreement(topk_actual, topk_surr)
        if topk_spearman is not None:
            logger.info(
                "top-k agreement n=%d spearman=%.4f",
                len(topk_surr),
                topk_spearman,
            )

        # Persist metrics
        metrics_path = cache_dir / "surrogate_metrics.jsonl"
        metrics_record = {
            "timestamp": datetime.now(UTC).isoformat(),
            **to_dict(cv_metrics),
            "topk_spearman": topk_spearman,
            "topk_n": len(topk_surr),
        }
        with metrics_path.open("a") as f:
            f.write(json.dumps(metrics_record) + "\n")
        logger.info(
            "wrote surrogate metrics path=%s", metrics_path
        )

    # Merge into ScoredJob objects
    scored = []
    for job in top_jobs:
        data = topk_results.get(job.hash)
        if data is None:
            continue
        scored.append(
            ScoredJob(
                **to_dict(job),
                score_interest=data.interest,
                score_fit=data.fit,
            )
        )

    # Sort by priority (candidate * recruiter) descending
    def _priority(j: ScoredJob) -> float:
        return (j.score_interest.score / 100) * (
            j.score_fit.score / 100
        )

    scored.sort(key=_priority, reverse=True)

    with output_path.open("w") as f:
        for job in scored:
            f.write(json.dumps(to_dict(job)) + "\n")
    logger.info(
        "wrote scored jobs count=%d path=%s",
        len(scored),
        output_path,
    )

    if report:
        from job_scraper.linkedin import load as load_linkedin
        from job_scraper.report import render_report

        lookup = load_linkedin(linkedin_dir)
        report_path = output_dir / "report.html"
        render_report(scored, report_path, lookup=lookup)
        logger.info("wrote report path=%s", report_path)


@app.command()
def run(
    cache_dir: Annotated[Path, typer.Option(help="Cache directory")] = Path(
        "data/cache"
    ),
    output_dir: Annotated[Path, typer.Option(help="Output directory")] = Path(
        "data/output"
    ),
    scrape_ttl: Annotated[
        int, typer.Option(help="Scrape cache TTL in seconds")
    ] = 86400,
    model: Annotated[
        str, typer.Option(help="Claude model for scoring")
    ] = "claude-haiku-4-5-20251001",
    rubric_model: Annotated[
        str,
        typer.Option(help="Claude model for rubric generation"),
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
    top_k: Annotated[
        int,
        typer.Option(help="Keep at most K jobs for LLM scoring"),
    ] = 200,
    linkedin_dir: Annotated[
        Path, typer.Option(help="LinkedIn data directory")
    ] = Path("linkedin"),
    resume: Annotated[
        Path, typer.Option(help="Path to resume for recruiter scoring")
    ] = Path("resume.md"),
    dedup_fields: Annotated[
        str,
        typer.Option(help="Comma-separated Job fields for dedup"),
    ] = "title,company,team,description",
    scrape_only: Annotated[
        bool,
        typer.Option(
            "--scrape-only",
            help="Scrape only, write jobs_raw.jsonl",
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
    num_cold_start: Annotated[
        int,
        typer.Option(
            help="Jobs to sample for initial surrogate training"
        ),
    ] = 200,
    num_explore: Annotated[
        int,
        typer.Option(
            help="Jobs to explore per active learning iteration"
        ),
    ] = 30,
    num_active_iters: Annotated[
        int,
        typer.Option(
            help="Active learning iterations during cold start"
        ),
    ] = 10,
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
        valid = {name for name, _, _ in discover()}
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
        bad = [f for f in fields if f not in job_attrs]
        if bad:
            raise typer.BadParameter(
                f"Unknown Job fields: {', '.join(bad)}"
            )
    else:
        fields = ()
    asyncio.run(
        _run(
            cache_dir=cache_dir,
            output_dir=output_dir,
            scrape_ttl=scrape_ttl,
            model=model,
            rubric_model=rubric_model,
            preferences_path=preferences,
            max_concurrent=max_concurrent,
            max_concurrent_api=max_concurrent_api,
            skip_score=skip_score,
            report=report,
            keywords=keywords,
            top_k=top_k,
            linkedin_dir=linkedin_dir,
            dedup_fields=fields,
            resume_path=resume,
            scrape_only=scrape_only,
            input_jobs=input_jobs,
            status_report=status_report,
            only=only_set,
            exclude=exclude_set,
            num_cold_start=num_cold_start,
            num_explore=num_explore,
            num_active_iters=num_active_iters,
        )
    )


if __name__ == "__main__":
    app()
