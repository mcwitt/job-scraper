import re

from rank_bm25 import BM25Okapi

from job_scraper.models import Job


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into words."""
    return re.findall(r"[a-z0-9]+", text.lower())


def score_relevance(
    keywords: list[str], jobs: list[Job]
) -> list[tuple[Job, float]]:
    """BM25-score each job against keyword query terms.

    Returns all jobs paired with normalized relevance scores (0-1).
    """
    if not jobs:
        return []

    query_tokens = []
    for phrase in keywords:
        query_tokens.extend(_tokenize(phrase))

    # Title repeated for boost
    corpus = [
        _tokenize(f"{job.title} {job.title} {job.description}")
        for job in jobs
    ]

    bm25 = BM25Okapi(corpus)
    raw_scores = bm25.get_scores(query_tokens)

    lo, hi = float(min(raw_scores)), float(max(raw_scores))
    if hi == lo:
        return [(job, 1.0) for job in jobs]

    return [
        (job, (score - lo) / (hi - lo))
        for job, score in zip(jobs, raw_scores)
    ]
