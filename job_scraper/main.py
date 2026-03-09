import asyncio
import json
import statistics
from pathlib import Path
from typing import Annotated

import httpx
import typer

from job_scraper.cache import open_cache
from job_scraper.models import Job
from job_scraper.relevance import score_relevance
from job_scraper.scraper import ScrapeFn, discover
from job_scraper.scraper.http import Http

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
    min_relevance: float,
    top_k: int | None,
    linkedin_dir: Path,
) -> None:
    scrape_cache_path = cache_dir / "scrape.jsonl"
    score_cache_path = cache_dir / "scores.jsonl"
    output_path = output_dir / "jobs.jsonl"

    output_dir.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(max_concurrent)

    scrapers = discover()
    if not scrapers:
        print("No scrapers found in job_scraper/scraper/.")
        return
    print(
        f"Discovered {len(scrapers)} scrapers: "
        f"{', '.join(name for name, _ in scrapers)}"
    )

    async with (
        httpx.AsyncClient(follow_redirects=True, timeout=30) as client,
        open_cache(scrape_cache_path, ttl=scrape_ttl) as scrape_cache,
    ):
        cache_get, cache_put = scrape_cache
        http = Http(client, cache_get, cache_put, semaphore)

        # Scrape all sources concurrently
        all_jobs: list[Job] = []

        async def collect(name: str, fn: ScrapeFn) -> list[Job]:
            jobs = []
            async for job in fn(http):
                jobs.append(job)
            print(f"  {name}: {len(jobs)} jobs")
            return jobs

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(collect(name, fn)) for name, fn in scrapers]

        for task in tasks:
            all_jobs.extend(task.result())

        # Write raw scraped jobs before filtering
        raw_path = output_dir / "jobs_raw.jsonl"
        with raw_path.open("w") as f:
            for job in all_jobs:
                f.write(json.dumps(job.to_dict()) + "\n")
        print(f"Wrote {len(all_jobs)} raw jobs to {raw_path}")

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
                d = job.to_dict()
                d["relevance"] = round(rel, 4)
                f.write(json.dumps(d) + "\n")
        print(f"Wrote {len(scored_rel)} jobs with relevance to {rel_path}")

        # Filter by threshold
        passing = [(j, r) for j, r in scored_rel if r >= min_relevance]
        passing.sort(key=lambda x: x[1], reverse=True)
        if top_k is not None:
            passing = passing[:top_k]

        scores = [r for _, r in scored_rel]
        print(
            f"Relevance: {len(all_jobs)} total, "
            f"{len(passing)} pass >= {min_relevance}"
            f" (min={min(scores):.3f}, max={max(scores):.3f},"
            f" median={statistics.median(scores):.3f})"
        )
        all_jobs = [j for j, _ in passing]

        # Deduplicate by hash
        seen: dict[str, Job] = {}
        for job in all_jobs:
            if job.hash not in seen:
                seen[job.hash] = job
        unique_jobs = list(seen.values())
        print(f"{len(unique_jobs)} unique jobs after dedup.")

        if skip_score:
            # Write unsorted jobs
            with output_path.open("w") as f:
                for job in unique_jobs:
                    f.write(json.dumps(job.to_dict()) + "\n")
            print(f"Wrote {len(unique_jobs)} jobs to {output_path}")
            return

        # Score
        profile_text = profile_path.read_text()
        if not profile_text.strip():
            print(f"Warning: {profile_path} is empty. Scores may be meaningless.")

        import anthropic

        from job_scraper.scorer import score_jobs

        async with open_cache(score_cache_path, ttl=0) as score_cache:
            ai = anthropic.AsyncAnthropic()
            scored = await score_jobs(
                unique_jobs,
                profile_text,
                ai,
                model,
                batch_size,
                score_cache,
            )

        # Sort by score descending
        scored.sort(key=lambda j: j.score, reverse=True)

        with output_path.open("w") as f:
            for job in scored:
                f.write(json.dumps(job.to_dict()) + "\n")
        print(f"Wrote {len(scored)} scored jobs to {output_path}")

        if report:
            from job_scraper.linkedin import load as load_linkedin
            from job_scraper.report import render_report

            lookup = load_linkedin(linkedin_dir)
            report_path = output_dir / "report.html"
            render_report(scored, report_path, lookup=lookup)
            print(f"Report written to {report_path}")


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
    min_relevance: Annotated[
        float, typer.Option(help="Min BM25 relevance score (0-1)")
    ] = 0.1,
    top_k: Annotated[
        int | None,
        typer.Option(help="Keep at most K jobs by relevance"),
    ] = 100,
    linkedin_dir: Annotated[
        Path, typer.Option(help="LinkedIn data directory")
    ] = Path("linkedin"),
) -> None:
    """Scrape and score job postings."""
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
            min_relevance=min_relevance,
            top_k=top_k,
            linkedin_dir=linkedin_dir,
        )
    )


if __name__ == "__main__":
    app()
