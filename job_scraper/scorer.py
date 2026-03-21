import asyncio
import dataclasses
import hashlib
import json
import logging
from typing import Any

import anthropic
import dacite
from anthropic.types import (
    JSONOutputFormatParam,
    OutputConfigParam,
    TextBlock,
)

from job_scraper.cache import Cache
from job_scraper.companies import canonicalize
from job_scraper.models import Job, Score

logger = logging.getLogger(__name__)


def _format_listing(job: Job) -> str:
    return (
        f"**{job.title}** at **{job.company}**\n"
        f"Location: {job.location or 'Not specified'}\n"
        f"Compensation: {job.comp or 'Not specified'}\n"
        f"Team: {job.team or 'Not specified'}\n\n"
        f"{job.description[:8000]}"
    )


def _schema_from_dataclass(cls: type) -> dict[str, Any]:
    """Build a JSON schema from a frozen dataclass."""
    props: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.type == "float" or f.type is float:
            props[f.name] = {"type": "number"}
        elif f.type == "int" or f.type is int:
            props[f.name] = {"type": "integer"}
        elif dataclasses.is_dataclass(f.type):
            props[f.name] = _schema_from_dataclass(f.type)  # type: ignore[arg-type]
        else:
            props[f.name] = {"type": "string"}
    return {
        "type": "object",
        "properties": props,
        "required": [f.name for f in dataclasses.fields(cls)],
        "additionalProperties": False,
    }


def _job_cache_key(system_prompt: str, job: Job) -> str:
    listing = _format_listing(job)
    raw = f"{system_prompt}\n{listing}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def score[T](
    job: Job,
    system: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    semaphore: asyncio.Semaphore,
    output_type: type[T],
    output_schema: dict[str, Any],
) -> T:
    """Score a single job via one API call."""
    user_msg = f"## Job Listing\n\n{_format_listing(job)}"

    async with semaphore:
        response = await client.messages.create(
            model=model,
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_msg}],
            output_config=OutputConfigParam(
                format=JSONOutputFormatParam(
                    type="json_schema",
                    schema=output_schema,
                ),
            ),
        )

    text_block = next(
        b for b in response.content if isinstance(b, TextBlock)
    )
    return dacite.from_dict(output_type, json.loads(text_block.text))


async def score_jobs[T](
    jobs: list[Job],
    client: anthropic.AsyncAnthropic,
    model: str,
    cache: Cache,
    system_prompt: str,
    output_type: type[T],
    label: str = "score",
    max_concurrent: int = 10,
) -> dict[str, T]:
    """Score jobs one-per-request, using cache to skip already-scored.

    Returns:
        Dict mapping job hash to typed dataclass instance.
    """
    output_schema = _schema_from_dataclass(output_type)
    semaphore = asyncio.Semaphore(max_concurrent)
    results: dict[str, T] = {}
    to_score: list[Job] = []

    cache_keys: dict[str, str] = {}
    for job in jobs:
        key = _job_cache_key(system_prompt, job)
        cache_keys[job.hash] = key

        cached = cache.get(key)
        if cached is not None:
            results[job.hash] = dacite.from_dict(
                output_type, cached
            )
        else:
            to_score.append(job)

    if not to_score:
        logger.info("%s all cached count=%d", label, len(results))
        return results

    logger.info(
        "%s jobs=%d cached=%d",
        label,
        len(to_score),
        len(results),
    )

    async def score_one(job: Job) -> None:
        try:
            result = await score(
                job,
                system_prompt,
                client,
                model,
                semaphore,
                output_type,
                output_schema,
            )
            results[job.hash] = result
            cache.put(
                cache_keys[job.hash],
                dataclasses.asdict(result),  # type: ignore[arg-type]
            )
        except Exception:
            logger.warning(
                "score failed job=%s title=%r company=%s",
                job.hash[:12],
                job.title,
                job.company,
                exc_info=True,
            )

    await asyncio.gather(*(
        score_one(job) for job in to_score
    ))

    return results


async def score_combined(
    jobs: list[Job],
    interest_rubric: str,
    fit_rubrics: dict[str, str],
    client: anthropic.AsyncAnthropic,
    model: str,
    cache: Cache,
    max_concurrent: int = 10,
) -> dict[str, Score]:
    """Score jobs using pre-generated rubrics.

    Groups jobs by company so same-company jobs share a
    system prompt (and thus Anthropic prompt cache).
    """

    def _system_for(company: str) -> str:
        canonical = canonicalize(company)
        fit = fit_rubrics.get(canonical, "")
        return (
            "You are assessing a job posting from two "
            "perspectives. For each field, write a 1-2 "
            "sentence assessment, then provide a summary "
            "and integer score 0-100.\n\n"
            "# Interest Rubric\n\n"
            + interest_rubric
            + "\n\n# Fit Rubric\n\n"
            + fit
        )

    by_company: dict[str, list[Job]] = {}
    for job in jobs:
        by_company.setdefault(job.company, []).append(job)

    all_results: dict[str, Score] = {}

    results_list = await asyncio.gather(*(
        score_jobs(
            company_jobs,
            client,
            model,
            cache,
            system_prompt=_system_for(company),
            output_type=Score,
            label=f"score/{company}",
            max_concurrent=max_concurrent,
        )
        for company, company_jobs in by_company.items()
    ))

    for results in results_list:
        all_results.update(results)

    return all_results
