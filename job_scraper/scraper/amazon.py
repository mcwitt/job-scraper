import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._html import html_to_text
from job_scraper.scraper._http import Http

logger = logging.getLogger(__name__)

_API = "https://www.amazon.jobs/en/search.json"
_PAGE_SIZE = 100
_CATEGORIES = [
    "software-development",
    "machine-learning-science",
    "data-science",
    "research-science",
]

_COMP_RE = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*-\s*([\d,]+(?:\.\d+)?)\s*USD\s*annually"
)


def _parse_posted(date_str: str) -> str | None:
    """Parse 'March 15, 2026' into '2026-03-15'."""
    try:
        return datetime.strptime(date_str, "%B %d, %Y").strftime(
            "%Y-%m-%d"
        )
    except (ValueError, TypeError):
        return None


def _extract_comp(qualifications: str) -> str | None:
    """Pull salary range from preferred_qualifications HTML."""
    m = _COMP_RE.search(qualifications)
    if not m:
        return None
    lo = m.group(1).replace(",", "")
    hi = m.group(2).replace(",", "")
    return f"${float(lo):,.0f} - ${float(hi):,.0f}"


def _build_description(job: dict) -> str:
    parts: list[str] = []
    for field in ("description", "basic_qualifications"):
        val = job.get(field, "")
        if val:
            parts.append(html_to_text(val))
    return "\n\n".join(parts)


async def scrape(http: Http) -> AsyncIterator[Job]:
    cat_params = "&".join(
        f"category%5B%5D={c}" for c in _CATEGORIES
    )
    seen: set[str] = set()

    offset = 0
    while True:
        url = (
            f"{_API}?result_limit={_PAGE_SIZE}&offset={offset}"
            f"&country=USA&sort=recent&{cat_params}"
        )
        try:
            body, scraped_at = await http.get(url)
        except httpx.TimeoutException:
            logger.warning("amazon: timeout at offset=%d, stopping", offset)
            break
        data = json.loads(body)
        jobs = data.get("jobs", [])
        if not jobs:
            break

        for j in jobs:
            icims_id = j.get("id_icims", "")
            if icims_id in seen:
                continue
            seen.add(icims_id)

            title = j.get("title", "")
            description = _build_description(j)
            location = j.get("location")
            team_obj = j.get("team") or {}
            team_label = team_obj.get("label", "")
            team = (
                team_label.replace("team-", "").replace("-", " ")
                if team_label
                else None
            )

            posted = _parse_posted(j.get("posted_date", ""))
            comp = _extract_comp(
                j.get("preferred_qualifications", "")
            )
            job_path = j.get("job_path", "")
            job_url = (
                f"https://www.amazon.jobs{job_path}"
                if job_path
                else ""
            )

            h = job_hash(title, "Amazon", description)
            yield Job(
                hash=h,
                title=title,
                company="Amazon",
                team=team,
                url=job_url,
                posted=posted,
                comp=comp,
                location=location,
                description=description,
                source="amazon",
                scraped_at=scraped_at,
            )

        offset += _PAGE_SIZE
        total = data.get("hits", 0)
        if offset >= total:
            break


if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
