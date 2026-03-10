import json
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper.http import Http


def _format_compensation(comp: dict) -> str | None:
    return comp.get("compensationTierSummary") or None


def scrape_board(board: str, *, name: str):
    """Return a scrape function for an Ashby job board."""

    async def scrape(http: Http) -> AsyncIterator[Job]:
        url = (
            f"https://api.ashbyhq.com/posting-api/job-board/"
            f"{board}?includeCompensation=true"
        )
        body, scraped_at = await http.get(url)
        data = json.loads(body)
        for posting in data.get("jobs", []):
            title = posting.get("title", "")
            team = posting.get("department") or posting.get("team")
            description = posting.get("descriptionPlain", "")
            post_url = posting.get("jobUrl", "")
            published = posting.get("publishedAt")
            posted = published[:10] if published else None

            location = posting.get("location")

            comp_data = posting.get("compensation")
            comp = _format_compensation(comp_data) if comp_data else None

            h = job_hash(title, name, description)
            yield Job(
                hash=h,
                title=title,
                company=name,
                team=team,
                url=post_url,
                posted=posted,
                comp=comp,
                location=location,
                description=description,
                source=f"ashby:{board}",
                scraped_at=scraped_at,
            )

    return scrape
