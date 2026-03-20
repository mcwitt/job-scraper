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

from job_scraper import surrogate
from job_scraper.cache import open_cache
from job_scraper.models import Job, to_dict
from job_scraper.relevance import parse_query, prefilter
from job_scraper.scraper import ScrapeFn, discover
from job_scraper.scraper._http import Http

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
        cache_get, cache_put = scrape_cache
        http = Http(client, cache_get, cache_put, semaphore)

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

        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(
                    collect(
                        name,
                        fn,
                        dataclasses.replace(http, cache_ttl=ttl)
                        if ttl is not None
                        else http,
                    )
                )
                for name, fn, ttl in scrapers
            ]

        errors: list[dict] = []
        now_str = datetime.now(UTC).isoformat()
        for task in tasks:
            name, jobs, error = task.result()
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
    preferences_path: Path,
    max_concurrent: int,
    max_concurrent_api: int,
    skip_score: bool,
    report: bool,
    keywords_path: Path,
    top_k: int,
    explore_budget: int,
    linkedin_dir: Path,
    dedup_fields: tuple[str, ...],
    resume_path: Path,
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

    score_cache_path = cache_dir / "score_interest.jsonl"
    output_path = output_dir / "jobs.jsonl"

    # --- Boolean pre-filter (location + title exclusions) ---
    queries = parse_query(keywords_path.read_text())
    filtered = prefilter(queries, all_jobs)
    logger.info(
        "prefilter total=%d passing=%d",
        len(all_jobs),
        len(filtered),
    )

    # --- Deduplicate ---
    if dedup_fields:
        seen: dict[tuple[str | None, ...], int] = {}
        deduped: list[Job] = []
        for job in filtered:
            key = tuple(
                getattr(job, f) for f in dedup_fields
            )
            if key not in seen:
                seen[key] = len(deduped)
                deduped.append(job)
        logger.info(
            "dedup unique=%d fields=%s",
            len(deduped),
            ",".join(dedup_fields),
        )
        filtered = deduped

    # --- LLM scoring helper ---
    preferences_text = preferences_path.read_text()
    if not preferences_text.strip():
        logger.warning(
            "preferences is empty path=%s",
            preferences_path,
        )
    resume_text = resume_path.read_text()

    import anthropic

    from job_scraper.companies import load_companies
    from job_scraper.models import Fit, Interest, ScoredJob
    from job_scraper.scorer import (
        score_fit,
        score_interest,
    )

    companies = load_companies()
    fit_cache_path = cache_dir / "score_fit.jsonl"
    ai = anthropic.AsyncAnthropic()

    async def _llm_score(
        jobs: list[Job],
    ) -> tuple[dict, dict]:
        async with (
            open_cache(score_cache_path) as icache,
            open_cache(fit_cache_path) as fcache,
        ):
            return await asyncio.gather(
                score_interest(
                    jobs,
                    preferences_text,
                    ai,
                    model,
                    icache,
                    companies=companies,
                    max_concurrent=max_concurrent_api,
                ),
                score_fit(
                    jobs,
                    resume_text,
                    ai,
                    model,
                    fcache,
                    companies=companies,
                    max_concurrent=max_concurrent_api,
                ),
            )

    def _write_surrogate_jsonl(
        ranked: list[tuple[Job, float, float, float]],
    ) -> None:
        sur_path = output_dir / "jobs_surrogate.jsonl"
        with sur_path.open("w") as f:
            for job, combined, im, fm in ranked:
                d = to_dict(job)
                p = surrogate.SCORE_PRECISION
                d["surrogate_combined"] = round(
                    combined, p
                )
                d["surrogate_interest"] = round(im, p)
                d["surrogate_fit"] = round(fm, p)
                f.write(json.dumps(d) + "\n")
        logger.info(
            "wrote surrogate scores count=%d path=%s",
            len(ranked),
            sur_path,
        )

    def _log_distribution(
        scores: list[float], label: str
    ) -> None:
        if not scores:
            return
        s = sorted(scores)
        n = len(s)
        pcts = (50, 75, 90, 95)
        vals = {
            k: s[min(int(n * k / 100), n - 1)]
            for k in pcts
        }
        logger.info(
            "%s total=%d %s max=%.4f",
            label,
            n,
            " ".join(
                f"p{k}={v:.4f}" for k, v in vals.items()
            ),
            s[-1],
        )

    # --- Surrogate model ---
    cfg_hash = surrogate.config_hash(preferences_text, resume_text)
    surrogate_dir = cache_dir / "surrogate"
    loaded = surrogate.load(surrogate_dir, cfg_hash)

    if loaded is not None:
        # -- Warm start --
        vec, i_model, f_model, training_data = loaded
        logger.info("surrogate warm start")

        ranked = surrogate.predict(
            vec, i_model, f_model, filtered
        )
        _log_distribution(
            [c for _, c, *_ in ranked], "surrogate"
        )
        _write_surrogate_jsonl(ranked)

        if skip_score:
            top_jobs = [j for j, *_ in ranked[:top_k]]
            with output_path.open("w") as f:
                for job in top_jobs:
                    f.write(
                        json.dumps(to_dict(job)) + "\n"
                    )
            logger.info(
                "wrote jobs count=%d path=%s",
                len(top_jobs),
                output_path,
            )
            return

        # Report jobs: top-k by surrogate prediction
        top_jobs = [j for j, *_ in ranked[:top_k]]

        # Exploration jobs: for surrogate improvement
        explore_jobs = surrogate.select_explore(
            ranked, top_k, explore_budget
        )
        all_to_score = top_jobs + explore_jobs
        logger.info(
            "scoring report=%d explore=%d total=%d",
            len(top_jobs),
            len(explore_jobs),
            len(all_to_score),
        )

        interest_scores, fit_scores = await _llm_score(
            all_to_score
        )

        surrogate.evaluate(
            ranked, interest_scores, fit_scores
        )

        training_data = surrogate.augment_training_data(
            all_to_score,
            interest_scores,
            fit_scores,
            training_data,
        )
        vec, i_model, f_model = surrogate.train(
            training_data
        )
        surrogate.save(
            surrogate_dir,
            vec,
            i_model,
            f_model,
            training_data,
            cfg_hash,
        )

    else:
        # -- Cold start --
        logger.info("surrogate cold start")

        if skip_score:
            with output_path.open("w") as f:
                for job in filtered:
                    f.write(
                        json.dumps(to_dict(job)) + "\n"
                    )
            logger.warning(
                "no surrogate model, wrote all filtered"
                " jobs count=%d",
                len(filtered),
            )
            return

        bootstrap = surrogate.sample(filtered, 500)
        logger.info(
            "bootstrap sample=%d total=%d",
            len(bootstrap),
            len(filtered),
        )

        interest_scores, fit_scores = await _llm_score(
            bootstrap
        )

        training_data = surrogate.augment_training_data(
            bootstrap, interest_scores, fit_scores, []
        )
        logger.info(
            "bootstrap training_data=%d",
            len(training_data),
        )

        vec, i_model, f_model = surrogate.train(
            training_data
        )

        ranked = surrogate.predict(
            vec, i_model, f_model, filtered
        )
        _log_distribution(
            [c for _, c, *_ in ranked], "surrogate"
        )
        _write_surrogate_jsonl(ranked)

        # Score top-k for report (cache skips bootstrap)
        top_jobs = [j for j, *_ in ranked[:top_k]]
        i_topk, f_topk = await _llm_score(top_jobs)
        interest_scores.update(i_topk)
        fit_scores.update(f_topk)

        surrogate.evaluate(
            ranked, interest_scores, fit_scores
        )

        training_data = surrogate.augment_training_data(
            top_jobs,
            interest_scores,
            fit_scores,
            training_data,
        )
        vec, i_model, f_model = surrogate.train(
            training_data
        )
        surrogate.save(
            surrogate_dir,
            vec,
            i_model,
            f_model,
            training_data,
            cfg_hash,
        )

    # --- Merge into ScoredJob objects ---
    scored = []
    for job in top_jobs:
        interest_data = interest_scores.get(job.hash)
        fit_data = fit_scores.get(job.hash)
        if interest_data is None or fit_data is None:
            continue
        scored.append(
            ScoredJob(
                **to_dict(job),
                score_interest=Interest(**interest_data),
                score_fit=Fit(**fit_data),
            )
        )

    def _priority(j: ScoredJob) -> float:
        return j.score_interest.score * j.score_fit.score

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
    keywords: Annotated[Path, typer.Option(help="Path to keywords file")] = Path(
        "keywords"
    ),
    top_k: Annotated[
        int,
        typer.Option(help="Score and report top K jobs"),
    ] = 100,
    explore_budget: Annotated[
        int,
        typer.Option(
            help="Extra jobs to LLM-score for surrogate improvement"
        ),
    ] = 20,
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
        typer.Option("--scrape-only", help="Scrape only, write jobs_raw.jsonl"),
    ] = False,
    input_jobs: Annotated[
        Path | None,
        typer.Option(help="Skip scrape, read raw jobs from JSONL file"),
    ] = None,
    status_report: Annotated[
        bool,
        typer.Option(
            "--status-report", help="Generate scraper status HTML report"
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
        typer.Option(help="Comma-separated scraper names to exclude"),
    ] = None,
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
        frozenset(s.strip() for s in only.split(",")) if only else None
    )
    exclude_set: frozenset[str] | None = (
        frozenset(s.strip() for s in exclude.split(",")) if exclude else None
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
            preferences_path=preferences,
            max_concurrent=max_concurrent,
            max_concurrent_api=max_concurrent_api,
            skip_score=skip_score,
            report=report,
            keywords_path=keywords,
            top_k=top_k,
            explore_budget=explore_budget,
            linkedin_dir=linkedin_dir,
            dedup_fields=fields,
            resume_path=resume,
            scrape_only=scrape_only,
            input_jobs=input_jobs,
            status_report=status_report,
            only=only_set,
            exclude=exclude_set,
        )
    )


if __name__ == "__main__":
    app()
