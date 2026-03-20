import logging
import sqlite3

from job_scraper.models import Job

logger = logging.getLogger(__name__)


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


def prefilter(
    queries: list[str], jobs: list[Job]
) -> list[Job]:
    """Boolean pre-filter using FTS5 MATCH.

    Returns jobs that match ANY query group (union).
    No scoring — just pass/fail.
    """
    if not jobs or not queries:
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
                (
                    i,
                    job.title,
                    job.description,
                    job.company,
                    job.location or "",
                )
                for i, job in enumerate(jobs)
            ),
        )

        passing: set[int] = set()
        for query in queries:
            try:
                rows = conn.execute(
                    "SELECT rowid FROM docs"
                    " WHERE docs MATCH ?",
                    (query,),
                ).fetchall()
            except sqlite3.OperationalError as e:
                raise ValueError(
                    f"Bad FTS5 query: {query!r}"
                ) from e
            passing.update(r[0] for r in rows)
            logger.info(
                "prefilter group matched=%d"
                " cumulative=%d",
                len(rows),
                len(passing),
            )
    finally:
        conn.close()

    return [jobs[i] for i in sorted(passing)]
