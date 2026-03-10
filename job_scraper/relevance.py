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

    Returns all jobs paired with raw BM25 scores, sorted
    descending by score.
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

    results = [
        (job, float(s))
        for job, s in zip(jobs, raw_scores, strict=True)
    ]
    results.sort(key=lambda x: x[1], reverse=True)
    return results
