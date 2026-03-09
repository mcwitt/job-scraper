import html
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._http import Http

_API = "https://explore.jobs.netflix.net/api/apply/v2/jobs"
_DOMAIN = "netflix.com"
_PAGE_SIZE = 100


def _html_to_text(raw: str) -> str:
    soup = BeautifulSoup(html.unescape(raw), "lxml")
    return soup.get_text(separator="\n", strip=True)


async def scrape(http: Http) -> AsyncIterator[Job]:
    now = datetime.now(timezone.utc).isoformat()

    # Paginate the list endpoint to collect all job IDs + metadata
    listings: list[tuple[int, dict]] = []
    start = 0
    while True:
        url = (
            f"{_API}?num={_PAGE_SIZE}&domain={_DOMAIN}"
            f"&sort_by=relevance&start={start}"
        )
        body = await http.get(url)
        data = json.loads(body)
        positions = data.get("positions", [])
        if not positions:
            break
        for p in positions:
            listings.append((p["id"], p))
        start += _PAGE_SIZE
        if start >= data.get("count", 0):
            break

    print(f"  netflix: {len(listings)} listings")

    # Fetch each job's detail page for the full description
    for job_id, meta in listings:
        detail_url = f"{_API}/{job_id}?domain={_DOMAIN}"
        body = await http.get(detail_url)
        detail = json.loads(body)

        title = detail.get("name", "")
        raw_desc = detail.get("job_description", "")
        description = _html_to_text(raw_desc) if raw_desc else ""

        location = detail.get("location")
        department = detail.get("department")
        post_url = detail.get("canonicalPositionUrl", "")

        t_create = detail.get("t_create")
        posted = None
        if t_create:
            posted = datetime.fromtimestamp(
                t_create, tz=timezone.utc
            ).strftime("%Y-%m-%d")

        h = job_hash(title, "Netflix", description)
        yield Job(
            hash=h,
            title=title,
            company="Netflix",
            team=department,
            url=post_url,
            posted=posted,
            comp=None,
            location=location,
            description=description,
            source="netflix",
            scraped_at=now,
        )
