import asyncio
import dataclasses
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import date
from typing import Any

import anthropic
from anthropic.types import (
    JSONOutputFormatParam,
    OutputConfigParam,
    TextBlock,
)

from job_scraper.companies import canonicalize
from job_scraper.models import Fit, Interest, Job

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
        else:
            props[f.name] = {"type": "string"}
    return {
        "type": "object",
        "properties": props,
        "required": [f.name for f in dataclasses.fields(cls)],
        "additionalProperties": False,
    }


def _job_cache_key(
    system_prompt: str,
    company_context: str,
    job: Job,
) -> str:
    listing = _format_listing(job)
    raw = f"{system_prompt}\n{company_context}\n{listing}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def score(
    job: Job,
    system: str,
    company_context: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    semaphore: asyncio.Semaphore,
    output_schema: dict[str, Any],
) -> dict[str, Any]:
    """Score a single job via one API call."""
    user_parts: list[str] = []
    if company_context:
        user_parts.append(
            f"## Company Context\n\n{company_context}"
        )
    user_parts.append(
        f"## Job Listing\n\n{_format_listing(job)}"
    )
    user_msg = "\n\n".join(user_parts)

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
    return json.loads(text_block.text)


async def score_jobs(
    jobs: list[Job],
    client: anthropic.AsyncAnthropic,
    model: str,
    cache: tuple[
        Callable[[str], dict[str, Any] | None],
        Callable[[str, dict[str, Any]], None],
    ],
    system_prompt: str,
    companies: dict[str, str],
    output_schema: dict[str, Any],
    max_concurrent: int = 10,
) -> dict[str, dict[str, Any]]:
    """Score jobs one-per-request, using cache to skip already-scored.

    Returns:
        Dict mapping job hash to parsed JSON dict.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    cache_get, cache_put = cache
    results: dict[str, dict[str, Any]] = {}
    to_score: list[Job] = []

    # Pre-compute per-job company context and cache keys
    job_info: dict[str, tuple[str, str]] = {}  # hash → (co_ctx, key)
    for job in jobs:
        co_ctx = companies.get(canonicalize(job.company), "")
        key = _job_cache_key(system_prompt, co_ctx, job)
        job_info[job.hash] = (co_ctx, key)

        cached = cache_get(key)
        if cached is not None:
            results[job.hash] = cached
        else:
            to_score.append(job)

    if not to_score:
        logger.info("all cached count=%d", len(results))
        return results

    logger.info(
        "scoring jobs=%d cached=%d",
        len(to_score),
        len(results),
    )

    missing = {
        job.company
        for job in to_score
        if not job_info[job.hash][0]
    }
    for name in sorted(missing):
        logger.warning("no company context company=%r", name)

    async def score_one(job: Job) -> None:
        co_ctx, cache_key = job_info[job.hash]
        try:
            result = await score(
                job,
                system_prompt,
                co_ctx,
                client,
                model,
                semaphore,
                output_schema,
            )
            results[job.hash] = result
            cache_put(cache_key, result)
        except Exception:
            logger.warning(
                "score failed job=%s title=%r company=%s",
                job.hash[:12],
                job.title,
                job.company,
                exc_info=True,
            )

    async with asyncio.TaskGroup() as tg:
        for job in to_score:
            tg.create_task(score_one(job))

    return results


_INTEREST_SCHEMA = _schema_from_dataclass(Interest)
_FIT_SCHEMA = _schema_from_dataclass(Fit)


async def score_interest(
    jobs: list[Job],
    preferences: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    cache: tuple[
        Callable[[str], dict[str, Any] | None],
        Callable[[str, dict[str, Any]], None],
    ],
    companies: dict[str, str] | None = None,
    max_concurrent: int = 10,
) -> dict[str, dict[str, Any]]:
    return await score_jobs(
        jobs,
        client,
        model,
        cache,
        system_prompt="""\
You are scoring a job posting from the candidate's perspective — how
excited and interested would this candidate be in this role?

For each field, write a 1-2 sentence assessment, then provide a
summary and score.

Fields to assess:
- **strengths_alignment**: Does the role leverage the candidate's
  existing strengths in ways that would be engaging and rewarding?
- **growth_opportunities**: Does the role offer development in areas
  the candidate has expressed interest in, even if they lack formal
  experience? A stated interest in compilers makes a compiler role
  appealing regardless of professional experience.
- **role_type_fit**: IC vs management, seniority level, day-to-day
  work matching the candidate's stated preferences.
- **company_reputation**: Is the company or team well-regarded
  in a field the candidate cares about?
- **compensation**: Does listed compensation (if any) fit the
  candidate's expected band for their experience level?
- **location**: Compatible with stated location preferences?
- **dealbreakers**: Anything that directly conflicts with stated
  preferences (required relocation, management-only, etc.).

Key principle: weight the candidate's *aspirations and interests*
heavily. A role in an area of strong stated interest should score
well even without professional experience there. A role matching
past experience but not stated interests should score lower.

**summary**: 1-2 sentence overall justification.

**score** 0.0-1.0:
- 0.9-1.0: Thrilled — strong alignment with strengths and growth
  interests
- 0.7-0.89: Genuinely appealing — good fit on most dimensions
- 0.4-0.69: Mixed — some appeal but significant preference gaps
- 0.0-0.39: Not interesting — poor alignment with what they want

## Candidate Preferences

"""
        + preferences,
        companies=companies or {},
        output_schema=_INTEREST_SCHEMA,
        max_concurrent=max_concurrent,
    )


async def score_fit(
    jobs: list[Job],
    resume: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    cache: tuple[
        Callable[[str], dict[str, Any] | None],
        Callable[[str, dict[str, Any]], None],
    ],
    companies: dict[str, str] | None = None,
    max_concurrent: int = 10,
) -> dict[str, dict[str, Any]]:
    return await score_jobs(
        jobs,
        client,
        model,
        cache,
        system_prompt="""\
The current month is """
        + date.today().strftime("%B %Y")
        + """.

You are a diligent tech recruiter screening a candidate against a job
posting. Assess how likely you would advance this candidate to a
recruiter screen based on their resume.

For each field, write a 1-2 sentence assessment, then provide a
summary and score.

Fields to assess:
- **demonstrated_experience**: Weight professional, on-the-job
  experience far more heavily than stated interests or hobby
  projects. Has the candidate *done this work* professionally?
- **institutional_credibility**: Consider the reputation of
  employers, academic institutions, and affiliations. Experience at
  a recognized lab or company in the relevant field carries weight.
- **depth_vs_adjacency**: Distinguish deep expertise (years of
  focused work) from adjacent experience (related but not directly
  applicable). Years building ETL pipelines is deep data engineering
  experience; stated interest in LLMs does not make an LLM engineer.
- **career_trajectory**: Does the candidate's progression show a
  clear path toward this role, or is this a significant pivot?
  Pivots without supporting evidence are risky.
- **minimum_qualifications**: Does the candidate meet stated
  requirements (years of experience, technologies, degree)?
- **seniority_alignment**: Does the candidate's level match?
- **location_visa**: Any logistical concerns?

Key principle: assess what is *verifiable on paper*, not what the
candidate aspires to. Stated interest without demonstrated
experience should not significantly boost the score.

**summary**: 1-2 sentence overall justification.

**score** 0.0-1.0:
- 0.9-1.0: Immediately schedule a screen — strong demonstrated fit
- 0.7-0.89: Likely advance — most requirements clearly met on paper
- 0.4-0.69: Borderline — some gaps, worth considering if pool thin
- 0.0-0.39: Would not advance — significant gaps in demonstrated
  experience

## Candidate Resume

"""
        + resume,
        companies=companies or {},
        output_schema=_FIT_SCHEMA,
        max_concurrent=max_concurrent,
    )
