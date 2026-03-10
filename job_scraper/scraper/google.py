import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._html import html_to_text
from job_scraper.scraper._http import Http

_BASE = "https://www.google.com/about/careers/applications"
_LIST = f"{_BASE}/jobs/results/"
_DS1_RE = re.compile(
    r"AF_initDataCallback\(\{key: 'ds:1'.*?data:(\[.+?\]),"
    r" sideChannel",
    re.DOTALL,
)
_PAGE_SIZE = 20


def _parse_page(body: str) -> tuple[list[list], int]:
    """Extract job entries and total count from a results page."""
    m = _DS1_RE.search(body)
    if not m:
        return [], 0
    raw = m.group(1).replace("\n", "\\n").replace("\r", "\\r")
    raw = raw.replace("\t", "\\t")
    data = json.loads(raw)
    jobs = data[0] or []
    total = data[2] if len(data) > 2 else 0
    return jobs, total


def _slugify(title: str) -> str:
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _build_job(entry: list, now: str) -> Job:
    job_id = entry[0]
    title = entry[1]
    company = entry[7] or "Google"

    # Location: entry[9] is a list of [display_name, ...]
    locations = entry[9]
    location = (
        "; ".join(loc[0] for loc in locations if loc and loc[0])
        if locations
        else None
    )

    # Description = about + responsibilities + qualifications
    parts: list[str] = []
    for idx in (10, 3, 4):
        if idx < len(entry) and entry[idx] and entry[idx][1]:
            parts.append(html_to_text(entry[idx][1]))
    description = "\n\n".join(parts)

    # Posted timestamp: entry[12] = [epoch_seconds, nanos]
    posted = None
    if len(entry) > 12 and entry[12]:
        ts = entry[12][0]
        posted = datetime.fromtimestamp(ts, tz=UTC).strftime(
            "%Y-%m-%d"
        )

    slug = _slugify(title)
    url = f"{_LIST}{job_id}-{slug}"

    h = job_hash(title, company, description)
    return Job(
        hash=h,
        title=title,
        company=company,
        team=None,
        url=url,
        posted=posted,
        comp=None,
        location=location,
        description=description,
        source="google",
        scraped_at=now,
    )


async def scrape(http: Http) -> AsyncIterator[Job]:
    now = datetime.now(UTC).isoformat()

    page = 1
    total: int | None = None
    count = 0
    while True:
        url = f"{_LIST}?page={page}"
        body = await http.get(url)
        jobs, reported_total = _parse_page(body)
        if total is None:
            total = reported_total
        if not jobs:
            break
        for entry in jobs:
            yield _build_job(entry, now)
            count += 1
        page += 1
        if count >= (total or 0):
            break
