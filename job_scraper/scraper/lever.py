import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper.http import Http


def _format_salary(salary: dict) -> str | None:
    lo = salary.get("min")
    hi = salary.get("max")
    currency = salary.get("currency", "")
    interval = salary.get("interval", "")
    if lo is None and hi is None:
        return None
    parts = []
    if lo is not None:
        parts.append(f"{currency} {lo:,.0f}")
    if hi is not None:
        parts.append(f"{currency} {hi:,.0f}")
    return " - ".join(parts) + (f" / {interval}" if interval else "")


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

            salary = posting.get("salaryRange")
            comp = _format_salary(salary) if salary else None

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
                source=f"lever:{company}",
                scraped_at=resp.fetched_at,
            )

    return scrape
