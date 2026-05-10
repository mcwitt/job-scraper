import asyncio
import json
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper.html import html_to_text
from job_scraper.scraper.http import Http


def _format_location(loc: dict) -> str | None:
    city = loc.get("city")
    region = loc.get("region")
    country = loc.get("country")
    parts = [p for p in (city, region, country) if p]
    return ", ".join(parts) if parts else None


def scrape_board(board: str, *, name: str):
    """Return a scrape function for a Workable job board."""

    async def scrape(http: Http) -> AsyncIterator[Job]:
        list_url = f"https://apply.workable.com/api/v3/accounts/{board}/jobs"

        # Phase 1: paginate listings via POST (cursor-based)
        stubs: list[dict] = []
        token: str | None = None
        while True:
            payload: dict = {"token": token} if token else {}
            resp = await http.post(list_url, json=payload)
            data = json.loads(resp.body)
            stubs.extend(data.get("results", []))
            token = data.get("nextPage")
            if not token:
                break

        # Phase 2: fetch detail pages concurrently (cached)
        async def fetch_detail(shortcode: str) -> str:
            detail_url = (
                f"https://apply.workable.com/api/v2/accounts/{board}/jobs/{shortcode}"
            )
            detail_resp = await http.get(detail_url)
            detail = json.loads(detail_resp.body)
            desc_html = detail.get("description", "")
            return html_to_text(desc_html) if desc_html else ""

        descriptions = await asyncio.gather(
            *(fetch_detail(p.get("shortcode", "")) for p in stubs)
        )

        for posting, description in zip(stubs, descriptions, strict=True):
            shortcode = posting.get("shortcode", "")
            title = posting.get("title", "")
            department = posting.get("department") or []
            team = department[0] if department else None
            published = posting.get("published")
            posted = published[:10] if published else None

            location = posting.get("location") or {}
            loc_str = _format_location(location)

            post_url = f"https://apply.workable.com/{board}/j/{shortcode}/"

            h = job_hash(title, name, description)
            yield Job(
                hash=h,
                title=title,
                company=name,
                team=team,
                url=post_url,
                posted=posted,
                compensation=None,
                location=loc_str,
                description=description,
                source=f"workable:{board}",
            )

    return scrape
