import html
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._http import Http


def _html_to_text(raw: str) -> str:
    unescaped = html.unescape(raw)
    soup = BeautifulSoup(unescaped, "lxml")
    return soup.get_text(separator="\n", strip=True)


def scrape_board(token: str):
    """Return a scrape function for a Greenhouse board."""

    async def scrape(http: Http) -> AsyncIterator[Job]:
        now = datetime.now(timezone.utc).isoformat()
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        body = await http.get(url)
        data = json.loads(body)
        for posting in data.get("jobs", []):
            title = posting.get("title", "")
            company = posting.get("company", {}).get("name", token)
            content = posting.get("content", "")
            description = _html_to_text(content) if content else ""

            departments = posting.get("departments", [])
            team = departments[0]["name"] if departments else None

            location_obj = posting.get("location", {})
            location = location_obj.get("name") if location_obj else None

            post_url = posting.get("absolute_url", "")

            updated = posting.get("updated_at")
            posted = updated[:10] if updated else None

            h = job_hash(title, company, description)
            yield Job(
                hash=h,
                title=title,
                company=company,
                team=team,
                url=post_url,
                posted=posted,
                comp=None,
                location=location,
                description=description,
                source=f"greenhouse:{token}",
                scraped_at=now,
            )

    return scrape
