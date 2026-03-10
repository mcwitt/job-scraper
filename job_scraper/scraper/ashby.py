import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper.http import Http


def _format_compensation(comp: dict) -> str | None:
    summary = comp.get("compensationTierSummary")
    if summary:
        return summary
    return None


def scrape_board(board: str):
    """Return a scrape function for an Ashby job board."""

    async def scrape(http: Http) -> AsyncIterator[Job]:
        now = datetime.now(UTC).isoformat()
        url = (
            f"https://api.ashbyhq.com/posting-api/job-board/"
            f"{board}?includeCompensation=true"
        )
        body = await http.get(url)
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

            company = posting.get("organizationName", board)
            h = job_hash(title, company, description)
            yield Job(
                hash=h,
                title=title,
                company=company,
                team=team,
                url=post_url,
                posted=posted,
                comp=comp,
                location=location,
                description=description,
                source=f"ashby:{board}",
                scraped_at=now,
            )

    return scrape
