import asyncio
import hashlib
import json
from collections.abc import Callable
from typing import Any

import anthropic
from anthropic.types import (
    JSONOutputFormatParam,
    OutputConfigParam,
    TextBlock,
)

from job_scraper.models import Job

RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hash": {"type": "string"},
                    "why": {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["hash", "why", "score"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scores"],
    "additionalProperties": False,
}


async def score_batch(
    jobs: list[Job],
    profile: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    semaphore: asyncio.Semaphore,
    system_prompt: str,
) -> dict[str, tuple[float, str]]:
    """Score a batch of jobs in a single API call.

    Returns:
        Dict mapping job hash to (score, why).
    """
    listings = []
    for job in jobs:
        listings.append(
            f"### {job.hash}\n"
            f"**{job.title}** at **{job.company}**\n"
            f"Location: {job.location or 'Not specified'}\n"
            f"Team: {job.team or 'Not specified'}\n\n"
            f"{job.description[:3000]}"
        )

    user_msg = "Score the following job postings:\n\n" + "\n\n---\n\n".join(listings)

    async with semaphore:
        response = await client.messages.create(
            model=model,
            max_tokens=16384,
            thinking={"type": "enabled", "budget_tokens": 4096},
            system=system_prompt.format(profile=profile),
            messages=[{"role": "user", "content": user_msg}],
            output_config=OutputConfigParam(
                format=JSONOutputFormatParam(
                    type="json_schema",
                    schema=RESPONSE_SCHEMA,
                ),
            ),
        )

    text_block = next(b for b in response.content if isinstance(b, TextBlock))
    result = json.loads(text_block.text)
    return {s["hash"]: (s["score"], s["why"]) for s in result["scores"]}


async def score_jobs(
    jobs: list[Job],
    profile: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    batch_size: int,
    cache: tuple[
        Callable[[str], dict[str, Any] | None],
        Callable[[str, dict[str, Any]], None],
    ],
    system_prompt: str,
    max_concurrent: int = 4,
) -> dict[str, tuple[float, str]]:
    """Score jobs, using cache to skip already-scored ones.

    Returns:
        Dict mapping job hash to (score, why).
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    cache_get, cache_put = cache
    results: dict[str, tuple[float, str]] = {}
    to_score: list[Job] = []

    # Include prompt+profile in cache key so edits invalidate cached scores
    context_hash = hashlib.sha256(
        (system_prompt + "\0" + profile).encode()
    ).hexdigest()[:12]

    for job in jobs:
        key = f"{job.hash}:{context_hash}"
        cached = cache_get(key)
        if cached is not None:
            results[job.hash] = (cached["score"], cached["why"])
        else:
            to_score.append(job)

    if to_score:
        batches = [
            to_score[i : i + batch_size]
            for i in range(0, len(to_score), batch_size)
        ]
        print(
            f"Scoring {len(to_score)} jobs "
            f"({len(results)} cached, {len(batches)} batches)..."
        )

        async def run_batch(
            batch_num: int, batch: list[Job]
        ) -> dict[str, tuple[float, str]]:
            print(f"  Batch {batch_num}: {len(batch)} jobs...")
            scores = await score_batch(
                batch, profile, client, model, semaphore, system_prompt
            )
            for job in batch:
                score_data = scores.get(job.hash)
                if score_data is None:
                    continue
                score, why = score_data
                cache_put(
                    f"{job.hash}:{context_hash}",
                    {"score": score, "why": why},
                )
            print(f"  Batch {batch_num}: done")
            return scores

        batch_tasks = [
            run_batch(i + 1, batch) for i, batch in enumerate(batches)
        ]
        for batch_scores in await asyncio.gather(*batch_tasks):
            results.update(batch_scores)

    return results


async def score_candidate(
    jobs: list[Job],
    profile: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    batch_size: int,
    cache: tuple[
        Callable[[str], dict[str, Any] | None],
        Callable[[str, dict[str, Any]], None],
    ],
    max_concurrent: int = 4,
) -> dict[str, tuple[float, str]]:
    return await score_jobs(
        jobs,
        profile,
        client,
        model,
        batch_size,
        cache,
        system_prompt="""\
You are a job-matching assistant. Score each job posting
against the candidate profile below.

For each job, evaluate:
- Skills alignment: how well the required skills match the candidate's experience
- Role type fit: IC vs management, seniority level, domain
- Location/remote compatibility
- Compensation fit (if listed)
- Red flags: dealbreakers, mismatches in values or preferences

Then return a score from 0.0-1.0:
- 0.9-1.0: Exceptional match — strong alignment on skills, interests, and preferences
- 0.7-0.89: Good match — mostly aligned, minor gaps
- 0.4-0.69: Partial match — some relevant aspects but significant mismatches
- 0.0-0.39: Poor match — does not align with the candidate's profile

Write "why" as a brief justification before assigning the score.

## Candidate Profile

{profile}
""",
        max_concurrent=max_concurrent,
    )


async def score_recruiter(
    jobs: list[Job],
    resume: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    batch_size: int,
    cache: tuple[
        Callable[[str], dict[str, Any] | None],
        Callable[[str, dict[str, Any]], None],
    ],
    max_concurrent: int = 4,
) -> dict[str, tuple[float, str]]:
    return await score_jobs(
        jobs,
        resume,
        client,
        model,
        batch_size,
        cache,
        system_prompt="""\
You are a tech recruiter screening resumes. For each job posting,
evaluate how likely you would move forward with this candidate
for a recruiter screen.

Consider:
- Does the candidate meet the stated minimum qualifications?
- Years of experience vs. what the role asks for
- Keyword and technology overlap with the job description
- Title / seniority alignment
- Location or visa concerns (if stated)

Score 0.0-1.0:
- 0.9-1.0: Strong match — would immediately schedule a screen
- 0.7-0.89: Likely move forward — most requirements met
- 0.4-0.69: Borderline — some gaps, might pass depending on pool
- 0.0-0.39: Would not advance — significant mismatch on requirements

Write "why" as a brief justification before assigning the score.

## Candidate Resume

{profile}
""",
        max_concurrent=max_concurrent,
    )
