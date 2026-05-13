import contextlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from job_scraper.hash import job_hash
from job_scraper.models import Compensation, Interval, Job
from job_scraper.scraper.html import html_to_text
from job_scraper.scraper.http import Http

API = "https://api.rippling.com/platform/api/ats/v1/board"

_FREQUENCY_MAP: dict[str, Interval] = {
    "YEAR": "annual",
    "MONTH": "monthly",
    "WEEK": "weekly",
    "HOUR": "hourly",
}


def _build_compensation(pay_ranges: list[dict]) -> Compensation | None:
    for r in pay_ranges:
        lo = r.get("rangeStart")
        hi = r.get("rangeEnd")
        if lo is None and hi is None:
            continue
        return Compensation(
            min_amount=int(lo) if lo is not None else None,
            max_amount=int(hi) if hi is not None else None,
            currency=r.get("currency") or None,
            interval=_FREQUENCY_MAP.get(r.get("frequency", ""), None),
        )
    return None


def scrape_board(slug: str, *, name: str):
    """Return a scrape function for a Rippling job board."""

    async def scrape(http: Http) -> AsyncIterator[Job]:
        resp = await http.get(f"{API}/{slug}/jobs")
        listings = json.loads(resp.body)

        # Dedupe by uuid (multi-location jobs repeat).
        seen: set[str] = set()
        for listing in listings:
            uuid = listing.get("uuid", "")
            if uuid in seen:
                continue
            seen.add(uuid)

            detail_resp = await http.get(
                f"{API}/{slug}/jobs/{uuid}"
            )
            detail = json.loads(detail_resp.body)

            title = detail.get("name", "")
            desc_parts = []
            desc = detail.get("description", {})
            if desc.get("role"):
                desc_parts.append(html_to_text(desc["role"]))
            if desc.get("company"):
                desc_parts.append(html_to_text(desc["company"]))
            description = "\n\n".join(desc_parts)

            dept = detail.get("department", {})
            team = dept.get("name") or dept.get("base_department")

            locations = detail.get("workLocations", [])
            location = ", ".join(locations) if locations else None

            post_url = detail.get("url", "")

            compensation = _build_compensation(
                detail.get("payRangeDetails") or []
            )

            created = detail.get("createdOn")
            posted = None
            if created:
                with contextlib.suppress(ValueError):
                    posted = (
                        datetime.fromisoformat(created)
                        .astimezone(UTC)
                        .date()
                        .isoformat()
                    )

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
                source=f"rippling:{slug}",
            )

    return scrape
