import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

import anthropic
from anthropic.types import (
    JSONOutputFormatParam,
    OutputConfigParam,
    TextBlock,
)

from job_scraper.companies import canonicalize
from job_scraper.models import Job

logger = logging.getLogger(__name__)


def _format_listing(job: Job) -> str:
    return (
        f"**{job.title}** at **{job.company}**\n"
        f"Location: {job.location or 'Not specified'}\n"
        f"Team: {job.team or 'Not specified'}\n\n"
        f"{job.description[:3000]}"
    )


def _company_section(
    companies: dict[str, str], jobs: list[Job]
) -> str:
    """Build company context section for unique companies in batch."""
    seen: dict[str, tuple[str, str]] = {}
    for job in jobs:
        canon = canonicalize(job.company)
        if canon not in seen and canon in companies:
            seen[canon] = (job.company, companies[canon])
    if not seen:
        return ""
    parts = [
        f"### {name}\n\n{content}" for name, content in seen.values()
    ]
    return "\n\n## Company Context\n\n" + "\n\n".join(parts)


def _job_cache_key(
    system_prompt: str,
    context: str,
    job: Job,
    company_context: str,
) -> str:
    """Compute a per-job cache key from all inputs relevant to this job."""
    listing = _format_listing(job)
    raw = f"{system_prompt}\n{context}\n{company_context}\n{listing}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def score_batch(
    jobs: list[Job],
    context: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    semaphore: asyncio.Semaphore,
    system_prompt: str,
    companies: dict[str, str],
) -> dict[str, tuple[float, str]]:
    """Score a batch of jobs in a single API call.

    Returns:
        Dict mapping job hash to (score, why).
    """
    listings = []
    for i, job in enumerate(jobs):
        listings.append(f"### {i}\n" + _format_listing(job))

    company_section = _company_section(companies, jobs)
    system = system_prompt.format(context=context) + company_section
    user_msg = "Score the following job postings:\n\n" + "\n\n---\n\n".join(
        listings
    )

    async with semaphore:
        response = await client.messages.create(
            model=model,
            max_tokens=16384,
            thinking={"type": "enabled", "budget_tokens": 4096},
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            output_config=OutputConfigParam(
                format=JSONOutputFormatParam(
                    type="json_schema",
                    schema={
                        "type": "object",
                        "properties": {
                            "scores": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "why": {"type": "string"},
                                        "score": {"type": "number"},
                                    },
                                    "required": ["id", "why", "score"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["scores"],
                        "additionalProperties": False,
                    },
                ),
            ),
        )

    text_block = next(b for b in response.content if isinstance(b, TextBlock))
    result = json.loads(text_block.text)
    return {
        jobs[s["id"]].hash: (s["score"], s["why"])
        for s in result["scores"]
        if 0 <= s["id"] < len(jobs)
    }


async def score_jobs(
    jobs: list[Job],
    context: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    batch_size: int,
    cache: tuple[
        Callable[[str], dict[str, Any] | None],
        Callable[[str, dict[str, Any]], None],
    ],
    system_prompt: str,
    companies: dict[str, str],
    max_concurrent: int = 10,
) -> dict[str, tuple[float, str]]:
    """Score jobs, using cache to skip already-scored ones.

    Returns:
        Dict mapping job hash to (score, why).
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    cache_get, cache_put = cache
    results: dict[str, tuple[float, str]] = {}
    to_score: list[Job] = []

    # Per-job cache keys based on inputs relevant to each job
    job_keys: dict[str, str] = {}
    for job in jobs:
        co_ctx = companies.get(canonicalize(job.company), "")
        key = _job_cache_key(system_prompt, context, job, co_ctx)
        job_keys[job.hash] = key

        cached = cache_get(key)
        if cached is not None:
            results[job.hash] = (cached["score"], cached["why"])
        else:
            to_score.append(job)

    if not to_score:
        logger.info("all cached count=%d", len(results))
        return results

    batches = [
        to_score[i : i + batch_size]
        for i in range(0, len(to_score), batch_size)
    ]
    logger.info(
        "scoring jobs=%d cached=%d batches=%d",
        len(to_score),
        len(results),
        len(batches),
    )

    # Log companies without context files
    missing = {
        job.company
        for job in to_score
        if canonicalize(job.company) not in companies
    }
    for name in sorted(missing):
        logger.warning("no company context company=%r", name)

    async def run_batch(
        batch_num: int, batch: list[Job]
    ) -> dict[str, tuple[float, str]]:
        logger.info("batch=%d jobs=%d", batch_num, len(batch))
        scores = await score_batch(
            batch, context, client, model, semaphore,
            system_prompt, companies,
        )
        for job in batch:
            score_data = scores.get(job.hash)
            if score_data is None:
                logger.warning(
                    "no score returned job=%s title=%r company=%s",
                    job.hash[:12],
                    job.title,
                    job.company,
                )
                continue
            score, why = score_data
            cache_put(
                job_keys[job.hash],
                {"score": score, "why": why},
            )
        return scores

    batch_tasks = [
        run_batch(i + 1, batch) for i, batch in enumerate(batches)
    ]
    for batch_scores in await asyncio.gather(*batch_tasks):
        results.update(batch_scores)

    return results


async def score_interest(
    jobs: list[Job],
    preferences: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    batch_size: int,
    cache: tuple[
        Callable[[str], dict[str, Any] | None],
        Callable[[str, dict[str, Any]], None],
    ],
    companies: dict[str, str] | None = None,
    max_concurrent: int = 10,
) -> dict[str, tuple[float, str]]:
    return await score_jobs(
        jobs,
        preferences,
        client,
        model,
        batch_size,
        cache,
        system_prompt="""\
You are scoring job postings from the candidate's perspective — how
excited and interested would this candidate be in this role?

Consider:
- **Strengths alignment**: Does the role leverage the candidate's
  existing strengths in ways that would be engaging and rewarding?
- **Growth opportunities**: Does the role offer development in areas
  the candidate has expressed interest in, even if they lack formal
  experience? A stated interest in compilers makes a compiler role
  appealing regardless of professional experience.
- **Role type fit**: IC vs management, seniority level, day-to-day
  work matching the candidate's stated preferences.
- **Company/team reputation**: Is the company or team well-regarded
  in a field the candidate cares about?
- **Compensation**: Does listed compensation (if any) fit the
  candidate's expected band for their experience level?
- **Location/remote**: Compatible with stated location preferences?
- **Dealbreakers**: Anything that directly conflicts with stated
  preferences (required relocation, management-only, etc.).

Key principle: weight the candidate's *aspirations and interests*
heavily. A role in an area of strong stated interest should score
well even without professional experience there. A role matching
past experience but not stated interests should score lower.

Score 0.0-1.0:
- 0.9-1.0: Thrilled — strong alignment with strengths and growth
  interests
- 0.7-0.89: Genuinely appealing — good fit on most dimensions
- 0.4-0.69: Mixed — some appeal but significant preference gaps
- 0.0-0.39: Not interesting — poor alignment with what they want

Write "why" as a brief justification before assigning the score.

## Candidate Preferences

{context}
""",
        companies=companies or {},
        max_concurrent=max_concurrent,
    )


async def score_fit(
    jobs: list[Job],
    resume: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    batch_size: int,
    cache: tuple[
        Callable[[str], dict[str, Any] | None],
        Callable[[str, dict[str, Any]], None],
    ],
    companies: dict[str, str] | None = None,
    max_concurrent: int = 10,
) -> dict[str, tuple[float, str]]:
    return await score_jobs(
        jobs,
        resume,
        client,
        model,
        batch_size,
        cache,
        system_prompt="""\
You are a diligent tech recruiter screening a candidate against job
postings. For each role, assess how likely you would advance this
candidate to a recruiter screen based on their resume.

Go beyond simple keyword matching:
- **Demonstrated experience**: Weight professional, on-the-job
  experience far more heavily than stated interests or hobby
  projects. Has the candidate *done this work* professionally?
- **Institutional credibility**: Consider the reputation of
  employers, academic institutions, and affiliations. Experience at
  a recognized lab or company in the relevant field carries weight.
- **Depth vs adjacency**: Distinguish deep expertise (years of
  focused work) from adjacent experience (related but not directly
  applicable). Years building ETL pipelines is deep data engineering
  experience; stated interest in LLMs does not make an LLM engineer.
- **Career trajectory**: Does the candidate's progression show a
  clear path toward this role, or is this a significant pivot?
  Pivots without supporting evidence are risky.
- **Minimum qualifications**: Does the candidate meet stated
  requirements (years of experience, technologies, degree)?
- **Seniority alignment**: Does the candidate's level match?
- **Location/visa**: Any logistical concerns?

Key principle: assess what is *verifiable on paper*, not what the
candidate aspires to. Stated interest without demonstrated
experience should not significantly boost the score.

Score 0.0-1.0:
- 0.9-1.0: Immediately schedule a screen — strong demonstrated fit
- 0.7-0.89: Likely advance — most requirements clearly met on paper
- 0.4-0.69: Borderline — some gaps, worth considering if pool thin
- 0.0-0.39: Would not advance — significant gaps in demonstrated
  experience

Write "why" as a brief justification before assigning the score.

## Candidate Resume

{context}
""",
        companies=companies or {},
        max_concurrent=max_concurrent,
    )
