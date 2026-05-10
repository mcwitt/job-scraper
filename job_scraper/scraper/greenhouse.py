import json
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper.html import html_to_text
from job_scraper.scraper.http import Http


def scrape_board(token: str, *, name: str):
    """Return a scrape function for a Greenhouse board."""

    async def scrape(http: Http) -> AsyncIterator[Job]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true&pay_transparency=true"
        resp = await http.get(url)
        data = json.loads(resp.body)
        for posting in data.get("jobs", []):
            title = posting.get("title", "")
            content = posting.get("content", "")
            description = html_to_text(content) if content else ""

            departments = posting.get("departments", [])
            team = departments[0]["name"] if departments else None

            location_obj = posting.get("location", {})
            location = location_obj.get("name") if location_obj else None

            post_url = posting.get("absolute_url", "")

            updated = posting.get("updated_at")
            posted = updated[:10] if updated else None

            h = job_hash(title, name, description)
            yield Job(
                hash=h,
                title=title,
                company=name,
                team=team,
                url=post_url,
                posted=posted,
                compensation=None,
                location=location,
                description=description,
                source=f"greenhouse:{token}",
            )

    return scrape
