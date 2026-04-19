import json
from collections.abc import AsyncIterator

from bs4 import BeautifulSoup

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper.html import html_to_text
from job_scraper.scraper.http import Http


def _extract_description(html_body: str) -> str:
    soup = BeautifulSoup(html_body, "lxml")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            raw = data.get("description", "")
            return html_to_text(raw) if raw else ""
    return ""


def scrape_board(company: str, *, name: str):
    """Return a scrape function for a Breezy HR job board."""

    async def scrape(http: Http) -> AsyncIterator[Job]:
        url = f"https://{company}.breezy.hr/json"
        resp = await http.get(url)
        postings = json.loads(resp.body)
        for posting in postings:
            title = posting.get("name", "")
            post_url = posting.get("url", "")
            team = posting.get("department") or None

            published = posting.get("published_date")
            posted = published[:10] if published else None

            loc = posting.get("location") or {}
            location = loc.get("name") or None

            page_resp = await http.get(post_url)
            description = _extract_description(page_resp.body)

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
                source=f"breezy:{company}",
            )

    return scrape
