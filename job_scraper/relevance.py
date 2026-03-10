import sqlite3

from job_scraper.models import Job


def parse_query(text: str) -> list[str]:
    """Parse keywords file into FTS5 query groups.

    Strip # comments and blank lines, concatenate remaining
    lines, split on --- separators.
    """
    groups: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "---":
            if current:
                groups.append(" ".join(current))
                current = []
            continue
        current.append(stripped)
    if current:
        groups.append(" ".join(current))
    return groups


def score_relevance(
    queries: list[str], jobs: list[Job]
) -> list[tuple[Job, float]]:
    """FTS5-score each job against query groups.

    Runs each query group independently, takes max score
    per job across groups. Returns all jobs with scores,
    sorted descending.
    """
    if not jobs:
        return []

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE docs USING fts5"
            "(title, description)"
        )
        conn.executemany(
            "INSERT INTO docs(rowid, title, description)"
            " VALUES (?, ?, ?)",
            (
                (i, job.title, job.description)
                for i, job in enumerate(jobs)
            ),
        )

        scores = [0.0] * len(jobs)
        for query in queries:
            # bm25 weights: title=5x, description=1x
            for rowid, bm25 in conn.execute(
                "SELECT rowid, bm25(docs, 5.0, 1.0)"
                " FROM docs WHERE docs MATCH ?",
                (query,),
            ):
                scores[rowid] = max(scores[rowid], -bm25)
    finally:
        conn.close()

    results = list(zip(jobs, scores, strict=True))
    results.sort(key=lambda x: x[1], reverse=True)
    return results
