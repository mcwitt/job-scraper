import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper.http import Http


def scrape_board(company: str, *, name: str, eu: bool = False):
    """Return a scrape function for a Lever job board."""

    async def scrape(http: Http) -> AsyncIterator[Job]:
        host = "api.eu.lever.co" if eu else "api.lever.co"
        url = f"https://{host}/v0/postings/{company}?mode=json"
        resp = await http.get(url)
        postings = json.loads(resp.body)
        for posting in postings:
            title = posting.get("text", "")
            categories = posting.get("categories", {})
            team = categories.get("team")
            description = posting.get("descriptionPlain", "")
            post_url = posting.get("hostedUrl", "")

            created = posting.get("createdAt")
            posted = (
                datetime.fromtimestamp(created / 1000, tz=UTC)
                .date()
                .isoformat()
                if created
                else None
            )

            location = categories.get("location")

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
                source=f"lever:{company}",
            )

    return scrape
