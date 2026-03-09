import json
from collections.abc import Callable
from typing import Any

import anthropic
from anthropic.types import (
    JSONOutputFormatParam,
    OutputConfigParam,
    TextBlock,
)

from job_scraper.models import Job, ScoredJob, scored_job

SYSTEM_PROMPT = """\
You are a job-matching assistant. Score each job posting
against the candidate profile below.

For each job, return a score from 0.0-1.0 and a brief justification.
- 0.9-1.0: Exceptional match — strong alignment on skills, interests, and preferences
- 0.7-0.89: Good match — mostly aligned, minor gaps
- 0.4-0.69: Partial match — some relevant aspects but significant mismatches
- 0.0-0.39: Poor match — does not align with the candidate's profile

## Candidate Profile

{profile}
"""

RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hash": {"type": "string"},
                    "score": {"type": "number"},
                    "why": {"type": "string"},
                },
                "required": ["hash", "score", "why"],
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
) -> dict[str, tuple[int, str]]:
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

    user_msg = (
        "Score the following job postings:\n\n" + "\n\n---\n\n".join(listings)
    )

    response = await client.messages.create(
        model=model,
        max_tokens=8192,
        thinking={"type": "enabled", "budget_tokens": 4096},
        system=SYSTEM_PROMPT.format(profile=profile),
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
) -> list[ScoredJob]:
    """Score jobs, using cache to skip already-scored ones."""
    cache_get, cache_put = cache
    results: list[ScoredJob] = []
    to_score: list[Job] = []

    for job in jobs:
        cached = cache_get(job.hash)
        if cached is not None:
            results.append(scored_job(job, cached["score"], cached["why"]))
        else:
            to_score.append(job)

    if to_score:
        print(f"Scoring {len(to_score)} jobs ({len(results)} cached)...")

    for i in range(0, len(to_score), batch_size):
        batch = to_score[i : i + batch_size]
        print(f"  Batch {i // batch_size + 1}: {len(batch)} jobs...")
        scores = await score_batch(batch, profile, client, model)
        for job in batch:
            score_data = scores.get(job.hash)
            if score_data is None:
                continue
            score, why = score_data
            cache_put(job.hash, {"score": score, "why": why})
            results.append(scored_job(job, score, why))

    return results
