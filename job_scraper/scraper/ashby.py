import json
import re
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Compensation, Interval, Job
from job_scraper.scraper.http import Http

_NUM = r"[\d,]+(?:\.\d+)?"
# Lookbehind keeps "CA$" / "AU$" out of the USD match so they fall to raw.
_USD = r"(?<![A-Za-z])\$"
_DASH = "[\u2013-]"

_RANGE_RE = re.compile(
    rf"{_USD}({_NUM})(K)?\s*{_DASH}\s*{_USD}?({_NUM})(K)?",
    re.IGNORECASE,
)
_SINGLE_RE = re.compile(rf"{_USD}({_NUM})(K)?", re.IGNORECASE)

_INTERVAL_KEYWORDS: dict[str, Interval] = {
    "hour": "hourly",
    "week": "weekly",
    "month": "monthly",
    "year": "annual",
    "annual": "annual",
}


def _to_int(num: str, k: str | None) -> int:
    n = float(num.replace(",", ""))
    return int(n * 1000) if k else int(n)


def _detect_interval(summary: str) -> Interval | None:
    s = summary.lower()
    for kw, interval in _INTERVAL_KEYWORDS.items():
        if kw in s:
            return interval
    return None


def _build_compensation(comp: dict) -> Compensation | str | None:
    summary = comp.get("compensationTierSummary") or ""
    if not summary:
        return None

    if m := _RANGE_RE.search(summary):
        lo_s, lo_k, hi_s, hi_k = m.groups()
        min_amount, max_amount = _to_int(lo_s, lo_k), _to_int(hi_s, hi_k)
    elif m := _SINGLE_RE.search(summary):
        num, k = m.groups()
        min_amount, max_amount = _to_int(num, k), None
    else:
        return summary

    return Compensation(
        min_amount=min_amount,
        max_amount=max_amount,
        currency="USD",
        interval=_detect_interval(summary),
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
