import html
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._http import Http


def _html_to_text(raw: str) -> str:
    unescaped = html.unescape(raw)
    soup = BeautifulSoup(unescaped, "lxml")
    return soup.get_text(separator="\n", strip=True)


def _format_pay(ranges: list[dict]) -> str | None:
    if not ranges:
        return None
    parts = []
    for r in ranges:
        lo = r.get("min_cents")
        hi = r.get("max_cents")
        currency = r.get("currency_type", "USD")
        if lo is None and hi is None:
            continue
        tokens = []
        if lo is not None:
            tokens.append(f"{currency} {lo / 100:,.0f}")
        if hi is not None:
            tokens.append(f"{currency} {hi / 100:,.0f}")
        parts.append(" - ".join(tokens))
    return " | ".join(parts) if parts else None


def scrape_board(token: str, *, name: str):
    """Return a scrape function for a Greenhouse board."""

    async def scrape(http: Http) -> AsyncIterator[Job]:
        now = datetime.now(UTC).isoformat()
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true&pay_transparency=true"
        body = await http.get(url)
        data = json.loads(body)
        for posting in data.get("jobs", []):
            title = posting.get("title", "")
            content = posting.get("content", "")
            description = _html_to_text(content) if content else ""

            departments = posting.get("departments", [])
            team = departments[0]["name"] if departments else None

            location_obj = posting.get("location", {})
            location = location_obj.get("name") if location_obj else None

            post_url = posting.get("absolute_url", "")

            updated = posting.get("updated_at")
            posted = updated[:10] if updated else None

            pay_ranges = posting.get("pay_input_ranges", [])
            comp = _format_pay(pay_ranges)

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
                source=f"greenhouse:{token}",
                scraped_at=now,
            )

    return scrape
