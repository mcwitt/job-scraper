import json
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._http import Http


def scrape_board(company: str, *, name: str):
    """Return a scrape function for a Gem job board."""

    async def scrape(http: Http) -> AsyncIterator[Job]:
        url = (
            f"https://api.gem.com/job_board/v0/{company}/job_posts/"
        )
        body, scraped_at = await http.get(url)
        postings = json.loads(body)
        for posting in postings:
            title = posting.get("title", "")
            description = posting.get("content_plain", "")
            post_url = posting.get("absolute_url", "")

            departments = posting.get("departments", [])
            team = departments[0]["name"] if departments else None

            location_obj = posting.get("location")
            location = location_obj.get("name") if location_obj else None

            published = posting.get("first_published_at")
            posted = published[:10] if published else None

            h = job_hash(title, name, description)
            yield Job(
                hash=h,
                title=title,
                company=name,
                team=team,
                url=post_url,
                posted=posted,
                comp=None,
                location=location,
                description=description,
                source=f"gem:{company}",
                scraped_at=scraped_at,
            )

    return scrape
