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
    TextBlockParam,
)

from job_scraper.cache import Cache
from job_scraper.companies import canonicalize
from job_scraper.llm import create
from job_scraper.models import Job, Score

logger = logging.getLogger(__name__)

SystemBlocks = list[TextBlockParam]


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


def _system_text(blocks: SystemBlocks) -> str:
    return "\n".join(b["text"] for b in blocks)


def _job_cache_key(system_blocks: SystemBlocks, job: Job) -> str:
    listing = _format_listing(job)
    raw = f"{_system_text(system_blocks)}\n{listing}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def score[T](
    job: Job,
    system: SystemBlocks,
    client: anthropic.AsyncAnthropic,
    model: str,
    semaphore: asyncio.Semaphore,
    output_type: type[T],
    output_schema: dict[str, Any],
    cache: Cache,
) -> T:
    """Score a single job via one API call (with cache)."""
    user_msg = f"## Job Listing\n\n{_format_listing(job)}"
    cache_key = _job_cache_key(system, job)

    text = await create(
        client,
        model,
        cache,
        cache_key,
        semaphore,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        output_config=OutputConfigParam(
            format=JSONOutputFormatParam(
                type="json_schema",
                schema=output_schema,
            ),
        ),
    )
    return dacite.from_dict(output_type, json.loads(text))


async def score_jobs[T](
    jobs: list[Job],
    client: anthropic.AsyncAnthropic,
    model: str,
    cache: Cache,
    system: SystemBlocks,
    output_type: type[T],
    label: str = "score",
    max_concurrent: int = 10,
) -> dict[str, T]:
    """Score jobs one-per-request, with transparent caching.

    Returns:
        Dict mapping job hash to typed dataclass instance.
    """
    output_schema = _schema_from_dataclass(output_type)
    semaphore = asyncio.Semaphore(max_concurrent)
    results: dict[str, T] = {}

    logger.info("%s jobs=%d", label, len(jobs))

    async def score_one(job: Job) -> None:
        try:
            result = await score(
                job,
                system,
                client,
                model,
                semaphore,
                output_type,
                output_schema,
                cache,
            )
            results[job.hash] = result
        except Exception:
            logger.warning(
                "score failed job=%s title=%r company=%s",
                job.hash[:12],
                job.title,
                job.company,
                exc_info=True,
            )

    await asyncio.gather(*(score_one(job) for job in jobs))

    return results


_FIT_SCORING = """\
# Fit Scoring

Assess how well the candidate fits this role from a recruiter's \
perspective. Use the candidate brief and company context (if \
provided) to evaluate each dimension. For each field, write a \
1-2 sentence assessment, then provide a summary and integer \
score 0-100.

## Dimensions

1. **demonstrated_experience** (25%) — Does the candidate have \
professional experience doing what this role requires?
2. **institutional_credibility** (15%) — Do the candidate's \
employers, degrees, and affiliations signal credibility for \
this role?
3. **depth_vs_adjacency** (20%) — Is the candidate's experience \
a direct match, or adjacent/transferable?
4. **career_trajectory** (10%) — Is this role a natural next \
step, or a significant pivot?
5. **minimum_qualifications** (15%) — Does the candidate meet \
stated requirements (years, technologies, degrees)?
6. **seniority_alignment** (10%) — Does the candidate's level \
match the role's level?
7. **location_visa** (5%) — Can the candidate work where the \
role requires?

## Score anchors

- 90-100: Immediately schedule a screen — strong demonstrated fit
- 70-89: Likely advance — most requirements clearly met on paper
- 40-69: Borderline — some gaps, worth considering if pool thin
- 0-39: Would not advance — significant gaps
"""


async def score_combined(
    jobs: list[Job],
    interest_rubric: str,
    candidate_brief: str,
    companies: dict[str, str],
    client: anthropic.AsyncAnthropic,
    model: str,
    cache: Cache,
    max_concurrent: int = 10,
) -> dict[str, Score]:
    """Score jobs using pre-generated rubrics.

    Groups jobs by company so same-company jobs share a
    system prompt (and thus Anthropic prompt cache).
    The shared prefix (rubric + scoring + brief) is marked
    for caching so it's reused across companies.
    """
    shared_prefix = (
        "You are assessing a job posting from two "
        "perspectives. For each field, write a 1-2 "
        "sentence assessment, then provide a summary "
        "and integer score 0-100.\n\n"
        "# Interest Rubric\n\n" + interest_rubric
        + "\n\n" + _FIT_SCORING
        + "\n# Candidate Brief\n\n" + candidate_brief
    )

    def _system_for(company: str) -> SystemBlocks:
        canonical = canonicalize(company)
        context = companies.get(canonical, "")
        blocks: SystemBlocks = [
            {
                "type": "text",
                "text": shared_prefix,
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if context:
            blocks.append({
                "type": "text",
                "text": "# Company Context\n\n" + context,
            })
        return blocks

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
            system=_system_for(company),
            output_type=Score,
            label=f"score/{company}",
            max_concurrent=max_concurrent,
        )
        for company, company_jobs in by_company.items()
    ))

    for results in results_list:
        all_results.update(results)

    return all_results
