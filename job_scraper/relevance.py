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
) -> list[tuple[Job, float, dict[int, float]]]:
    """FTS5-score each job against query groups.

    Runs each query group independently, takes max score
    per job across groups. Returns all jobs with
    (job, max_score, {group_index: score}), sorted
    descending by max_score.
    """
    if not jobs:
        return []

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE docs USING fts5"
            "(title, description, company, location)"
        )
        conn.executemany(
            "INSERT INTO docs(rowid, title, description,"
            " company, location) VALUES (?, ?, ?, ?, ?)",
            (
                (i, job.title, job.description,
                 job.company, job.location or "")
                for i, job in enumerate(jobs)
            ),
        )

        scores = [0.0] * len(jobs)
        group_scores: list[dict[int, float]] = [
            {} for _ in jobs
        ]
        for gi, query in enumerate(queries):
            # bm25 weights: title=5x, description=1x,
            # company=0x, location=3x
            try:
                rows = conn.execute(
                    "SELECT rowid, bm25(docs, 5.0, 1.0,"
                    " 0.0, 3.0) FROM docs WHERE docs"
                    " MATCH ?",
                    (query,),
                ).fetchall()
            except sqlite3.OperationalError as e:
                raise ValueError(
                    f"Bad FTS5 query: {query!r}"
                ) from e
            # Normalize within this group so each group
            # contributes on a 0-1 scale before MAX.
            group_max = max(
                (-bm25 for _, bm25 in rows), default=0.0
            )
            for rowid, bm25 in rows:
                normalized = (
                    -bm25 / group_max if group_max else 0.0
                )
                group_scores[rowid][gi] = normalized
                scores[rowid] = max(
                    scores[rowid], normalized
                )
    finally:
        conn.close()

    results = list(
        zip(jobs, scores, group_scores, strict=True)
    )
    results.sort(key=lambda x: x[1], reverse=True)
    return results
