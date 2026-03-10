import asyncio
import dataclasses
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer

from job_scraper.cache import open_cache
from job_scraper.models import Job, to_dict
from job_scraper.relevance import score_relevance
from job_scraper.scraper import ScrapeFn, discover
from job_scraper.scraper.http import Http

logger = logging.getLogger("job_scraper.main")
app = typer.Typer()


async def _run(
    cache_dir: Path,
    output_dir: Path,
    scrape_ttl: int,
    batch_size: int,
    model: str,
    profile_path: Path,
    max_concurrent: int,
    skip_score: bool,
    report: bool,
    keywords_path: Path,
    top_k: int,
    linkedin_dir: Path,
    dedup_fields: tuple[str, ...],
    resume_path: Path,
) -> None:
    scrape_cache_path = cache_dir / "scrape.jsonl"
    score_cache_path = cache_dir / "fit_candidate.jsonl"
    output_path = output_dir / "jobs.jsonl"

    output_dir.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(max_concurrent)

    scrapers = discover()
    if not scrapers:
        logger.warning("no scrapers found")
        return
    logger.info(
        "discovered scrapers count=%d names=%s",
        len(scrapers),
        ",".join(name for name, _, _ in scrapers),
    )

    async with (
        httpx.AsyncClient(follow_redirects=True, timeout=30) as client,
        open_cache(scrape_cache_path, ttl=scrape_ttl) as scrape_cache,
    ):
        cache_get, cache_put = scrape_cache
        http = Http(client, cache_get, cache_put, semaphore)

        # Scrape all sources concurrently
        all_jobs: list[Job] = []
        errors: list[dict] = []

        async def collect(name: str, fn: ScrapeFn, h: Http) -> list[Job]:
            try:
                jobs = []
                async for job in fn(h):
                    jobs.append(job)
                logger.info("scraper=%s jobs=%d", name, len(jobs))
                return jobs
            except Exception as exc:
                now_str = datetime.now(UTC).isoformat()
                logger.error("scraper=%s error=%s", name, exc)
                errors.append({
                    "scraper": name,
                    "timestamp": now_str,
                    "error": str(exc),
                })
                return []

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

        for task in tasks:
            all_jobs.extend(task.result())

        if errors:
            errors_path = output_dir / "scraper_errors.jsonl"
            with errors_path.open("w") as f:
                for err in errors:
                    f.write(json.dumps(err) + "\n")
            logger.warning(
                "scrapers_failed=%d path=%s", len(errors), errors_path
            )

        # Write raw scraped jobs before filtering
        raw_path = output_dir / "jobs_raw.jsonl"
        with raw_path.open("w") as f:
            for job in all_jobs:
                f.write(json.dumps(to_dict(job)) + "\n")
        logger.info("wrote raw jobs count=%d path=%s", len(all_jobs), raw_path)

        # BM25 relevance scoring
        kw_lines = keywords_path.read_text().splitlines()
        keywords = [
            ln.strip()
            for ln in kw_lines
            if ln.strip() and not ln.strip().startswith("#")
        ]
        scored_rel = score_relevance(keywords, all_jobs)

        # Write all jobs with relevance (observability)
        rel_path = output_dir / "jobs_relevance.jsonl"
        with rel_path.open("w") as f:
            for job, rel in scored_rel:
                d = to_dict(job)
                d["relevance"] = round(rel, 4)
                f.write(json.dumps(d) + "\n")
        logger.info(
            "wrote relevance scores count=%d path=%s",
            len(scored_rel),
            rel_path,
        )

        logger.info("relevance total=%d top_k=%d", len(all_jobs), top_k)

        # Deduplicate by selected fields (before top-k truncation)
        passing = scored_rel
        if dedup_fields:
            seen: dict[tuple[str | None, ...], int] = {}
            deduped: list[tuple[Job, float]] = []
            for job, rel in passing:
                key = tuple(getattr(job, f) for f in dedup_fields)
                if key not in seen:
                    seen[key] = len(deduped)
                    deduped.append((job, rel))
            logger.info(
                "dedup unique=%d fields=%s",
                len(deduped),
                ",".join(dedup_fields),
            )
            passing = deduped

        passing = passing[:top_k]
        unique_jobs = [j for j, _ in passing]

        if skip_score:
            # Write unsorted jobs
            with output_path.open("w") as f:
                for job in unique_jobs:
                    f.write(json.dumps(to_dict(job)) + "\n")
            logger.info(
                "wrote jobs count=%d path=%s", len(unique_jobs), output_path
            )
            return

        # Score
        profile_text = profile_path.read_text()
        if not profile_text.strip():
            logger.warning("profile is empty path=%s", profile_path)

        import anthropic

        from job_scraper.models import Score, ScoredJob, scored_job
        from job_scraper.scorer import (
            score_fit,
            score_interest,
        )

        recruiter_cache_path = cache_dir / "fit_recruiter.jsonl"
        ai = anthropic.AsyncAnthropic()

        async with open_cache(score_cache_path) as cand_cache:
            logger.info("scoring phase=interest")
            cand_scores = await score_interest(
                unique_jobs,
                profile_text,
                ai,
                model,
                batch_size,
                cand_cache,
            )

        resume_text = resume_path.read_text()
        async with open_cache(recruiter_cache_path) as rec_cache:
            logger.info("scoring phase=fit")
            rec_scores = await score_fit(
                unique_jobs,
                resume_text,
                ai,
                model,
                batch_size,
                rec_cache,
            )

        # Merge into ScoredJob objects
        scored = []
        for job in unique_jobs:
            cand = cand_scores.get(job.hash)
            if cand is None:
                continue
            rec = rec_scores.get(job.hash)
            scored.append(
                scored_job(
                    job,
                    fit_candidate=Score(*cand),
                    fit_recruiter=Score(*rec) if rec else None,
                )
            )

        # Sort by priority (candidate * recruiter) descending
        def _priority(j: ScoredJob) -> float:
            rv = j.fit_recruiter.value if j.fit_recruiter else j.fit_candidate.value
            return j.fit_candidate.value * rv

        scored.sort(key=_priority, reverse=True)

        with output_path.open("w") as f:
            for job in scored:
                f.write(json.dumps(to_dict(job)) + "\n")
        logger.info("wrote scored jobs count=%d path=%s", len(scored), output_path)

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
    batch_size: Annotated[int, typer.Option(help="Scoring batch size")] = 20,
    model: Annotated[
        str, typer.Option(help="Claude model for scoring")
    ] = "claude-haiku-4-5-20251001",
    profile: Annotated[Path, typer.Option(help="Path to candidate profile")] = Path(
        "profile.md"
    ),
    max_concurrent: Annotated[
        int, typer.Option(help="Max concurrent HTTP requests")
    ] = 5,
    skip_score: Annotated[
        bool, typer.Option("--skip-score", help="Skip scoring step")
    ] = False,
    report: Annotated[
        bool, typer.Option("--report", help="Generate HTML report")
    ] = False,
    keywords: Annotated[Path, typer.Option(help="Path to keywords file")] = Path(
        "keywords.txt"
    ),
    top_k: Annotated[
        int,
        typer.Option(help="Keep at most K jobs by relevance"),
    ] = 100,
    linkedin_dir: Annotated[
        Path, typer.Option(help="LinkedIn data directory")
    ] = Path("linkedin"),
    resume: Annotated[
        Path, typer.Option(help="Path to resume for recruiter scoring")
    ] = Path("resume.md"),
    dedup_fields: Annotated[
        str,
        typer.Option(help="Comma-separated Job fields for dedup"),
    ] = "title,company,team",
) -> None:
    """Scrape and score job postings."""
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
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
            batch_size=batch_size,
            model=model,
            profile_path=profile,
            max_concurrent=max_concurrent,
            skip_score=skip_score,
            report=report,
            keywords_path=keywords,
            top_k=top_k,
            linkedin_dir=linkedin_dir,
            dedup_fields=fields,
            resume_path=resume,
        )
    )


if __name__ == "__main__":
    app()
