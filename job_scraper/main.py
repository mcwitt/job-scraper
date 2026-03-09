import asyncio
import json
from pathlib import Path
from typing import Annotated

import httpx
import typer

from job_scraper.cache import open_cache
from job_scraper.models import Job
from job_scraper.scraper.greenhouse import scrape as greenhouse_scrape
from job_scraper.scraper.http import make_get

app = typer.Typer()


def _matches_keywords(job: Job, keywords: list[str]) -> bool:
    """Check if a job's title or description matches any keyword (case-insensitive)."""
    text = f"{job.title}\n{job.description}".lower()
    return any(kw in text for kw in keywords)


async def _run(
    boards: list[str],
    cache_dir: Path,
    output_dir: Path,
    scrape_ttl: int,
    batch_size: int,
    model: str,
    profile_path: Path,
    max_concurrent: int,
    skip_score: bool,
    report: bool,
    keywords: list[str],
) -> None:
    scrape_cache_path = cache_dir / "scrape.jsonl"
    score_cache_path = cache_dir / "scores.jsonl"
    output_path = output_dir / "jobs.jsonl"

    output_dir.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(max_concurrent)

    async with (
        httpx.AsyncClient(follow_redirects=True, timeout=30) as client,
        open_cache(scrape_cache_path, ttl=scrape_ttl) as scrape_cache,
    ):
        get = make_get(client, scrape_cache, semaphore)

        # Scrape all boards concurrently
        all_jobs: list[Job] = []

        async def collect_board(token: str) -> list[Job]:
            jobs = []
            async for job in greenhouse_scrape([token], get):
                jobs.append(job)
            return jobs

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(collect_board(token)) for token in boards]

        for task in tasks:
            all_jobs.extend(task.result())

        # Write raw scraped jobs before filtering
        raw_path = output_dir / "jobs_raw.jsonl"
        with raw_path.open("w") as f:
            for job in all_jobs:
                f.write(json.dumps(job.to_dict()) + "\n")
        print(f"Wrote {len(all_jobs)} raw jobs to {raw_path}")

        # Filter by keywords
        if keywords:
            normalized = [kw.lower() for kw in keywords]
            filtered = [j for j in all_jobs if _matches_keywords(j, normalized)]
            print(
                f"Scraped {len(all_jobs)} jobs, "
                f"{len(filtered)} match keywords {keywords}."
            )
            all_jobs = filtered

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
                unique_jobs, profile_text, ai, model, batch_size, score_cache
            )

        # Sort by score descending
        scored.sort(key=lambda j: j.score, reverse=True)

        with output_path.open("w") as f:
            for job in scored:
                f.write(json.dumps(job.to_dict()) + "\n")
        print(f"Wrote {len(scored)} scored jobs to {output_path}")

        if report:
            from job_scraper.report import render_report

            report_path = output_dir / "report.html"
            render_report(scored, report_path)
            print(f"Report written to {report_path}")


@app.command()
def run(
    boards: Annotated[list[str], typer.Argument(help="Greenhouse board tokens")],
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
    profile: Annotated[
        Path, typer.Option(help="Path to candidate profile")
    ] = Path("profile.txt"),
    max_concurrent: Annotated[
        int, typer.Option(help="Max concurrent HTTP requests")
    ] = 5,
    skip_score: Annotated[
        bool, typer.Option("--skip-score", help="Skip scoring step")
    ] = False,
    report: Annotated[
        bool, typer.Option("--report", help="Generate HTML report")
    ] = False,
    keywords: Annotated[
        list[str],
        typer.Option("--keyword", "-k", help="Filter job titles by keyword"),
    ] = [
        "software engineer",
        "scientist",
        "machine learning engineer",
        "ai engineer",
        "data scientist",
    ],
) -> None:
    """Scrape and score job postings from Greenhouse boards."""
    asyncio.run(
        _run(
            boards=boards,
            cache_dir=cache_dir,
            output_dir=output_dir,
            scrape_ttl=scrape_ttl,
            batch_size=batch_size,
            model=model,
            profile_path=profile,
            max_concurrent=max_concurrent,
            skip_score=skip_score,
            report=report,
            keywords=keywords,
        )
    )


if __name__ == "__main__":
    app()
