import sqlite3

from job_scraper.models import Job


def filter_relevant(
    query: str, jobs: list[Job]
) -> list[Job]:
    """Return jobs matching the FTS5 query."""
    if not jobs or not query:
        return list(jobs)

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
        matched = {rowid for (rowid,) in rows}
    finally:
        conn.close()

    return [jobs[i] for i in sorted(matched)]
