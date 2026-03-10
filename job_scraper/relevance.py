import re

import numpy as np
from rank_bm25 import BM25Okapi

from job_scraper.models import Job


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into words."""
    return re.findall(r"[a-z0-9]+", text.lower())


def score_relevance(
    keywords: list[str], jobs: list[Job]
) -> list[tuple[Job, float]]:
    """BM25-score each job against keyword query terms.

    Returns all jobs paired with percentile-rank scores (0-1).
    Each job's score is the fraction of jobs it scores above,
    so the result is stable against small corpus changes.
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

    n = len(raw_scores)
    ranks = np.argsort(np.argsort(raw_scores)).astype(float)
    percentiles = ranks / (n - 1) if n > 1 else np.ones(n)

    return [
        (job, float(p))
        for job, p in zip(jobs, percentiles, strict=True)
    ]
