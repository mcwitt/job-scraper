import json
import re
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Compensation, Job
from job_scraper.scraper.http import Http

_RANGE_RE = re.compile(
    r"\$([\d,]+)(K)?\s*[" + "\u2013" + r"-]\s*\$([\d,]+)(K)?",
    re.IGNORECASE,
)


def _to_int(num: str, k: str | None) -> int:
    n = int(num.replace(",", ""))
    return n * 1000 if k else n


def _build_compensation(comp: dict) -> Compensation | None:
    summary = comp.get("compensationTierSummary") or ""
    m = _RANGE_RE.search(summary)
    if not m:
        return None
    lo_s, lo_k, hi_s, hi_k = m.groups()
    return Compensation(
        min_amount=_to_int(lo_s, lo_k),
        max_amount=_to_int(hi_s, hi_k),
        currency="USD",
        interval=None,
        equity="Offers Equity" in summary,
        bonus="Offers Bonus" in summary,
    )


def scrape_board(board: str, *, name: str):
    """Return a scrape function for an Ashby job board."""

    async def scrape(http: Http) -> AsyncIterator[Job]:
        url = (
            f"https://api.ashbyhq.com/posting-api/job-board/"
            f"{board}?includeCompensation=true"
        )
        resp = await http.get(url)
        data = json.loads(resp.body)
        for posting in data.get("jobs", []):
            title = posting.get("title", "")
            team = posting.get("department") or posting.get("team")
            description = posting.get("descriptionPlain", "")
            post_url = posting.get("jobUrl", "")
            published = posting.get("publishedAt")
            posted = published[:10] if published else None

            location = posting.get("location")

            comp_data = posting.get("compensation")
            compensation = _build_compensation(comp_data) if comp_data else None

            h = job_hash(title, name, description)
            yield Job(
                hash=h,
                title=title,
                company=name,
                team=team,
                url=post_url,
                posted=posted,
                compensation=compensation,
                location=location,
                description=description,
                source=f"ashby:{board}",
            )

    return scrape
