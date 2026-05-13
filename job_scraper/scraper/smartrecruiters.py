import asyncio
import json
import logging
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Compensation, Interval, Job
from job_scraper.scraper.html import html_to_text
from job_scraper.scraper.http import Http

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100

_PERIOD_MAP: dict[str, Interval] = {
    "ANNUAL": "annual",
    "YEARLY": "annual",
    "MONTHLY": "monthly",
    "WEEKLY": "weekly",
    "HOURLY": "hourly",
}


def _build_compensation(comp: dict | None) -> Compensation | None:
    if not comp:
        return None
    lo = comp.get("min")
    hi = comp.get("max")
    # Skip placeholder zeros (SR sets unset fields to 0 rather than null).
    lo = lo if lo else None
    hi = hi if hi else None
    if lo is None and hi is None:
        return None
    return Compensation(
        min_amount=int(lo) if lo is not None else None,
        max_amount=int(hi) if hi is not None else None,
        currency=comp.get("currency") or None,
        interval=_PERIOD_MAP.get(comp.get("period", ""), None),
    )


def scrape_board(company: str, *, name: str):
    """Return a scrape function for a SmartRecruiters career site."""
    base = "https://api.smartrecruiters.com/v1/companies"
    list_url = f"{base}/{company}/postings"

    async def scrape(http: Http) -> AsyncIterator[Job]:
        # Phase 1: paginate listings
        stubs: list[tuple[str, str, str | None, str | None]] = []

        try:
            first_resp = await http.get(
                f"{list_url}?limit={_PAGE_SIZE}&offset=0"
            )
        except Exception:
            logger.warning(
                "company=%s offset=0 page_error=true",
                name,
            )
            return

        first = json.loads(first_resp.body)
        total = first.get("totalFound", 0)
        logger.info(
            "company=%s listings=%d",
            name,
            total,
        )

        def extract_stubs(postings: list[dict]) -> None:
            for p in postings:
                loc = p.get("location", {})
                stubs.append((
                    p.get("name", ""),
                    p.get("id", ""),
                    loc.get("fullLocation") if loc else None,
                    p.get("releasedDate"),
                ))

        extract_stubs(first.get("content", []))

        # Fetch remaining pages concurrently
        async def fetch_page(
            offset: int,
        ) -> list[dict]:
            try:
                resp = await http.get(
                    f"{list_url}?limit={_PAGE_SIZE}"
                    f"&offset={offset}"
                )
                return json.loads(resp.body).get("content", [])
            except Exception:
                logger.warning(
                    "company=%s offset=%d page_error=true",
                    name,
                    offset,
                )
                return []

        remaining = await asyncio.gather(*(
            fetch_page(off)
            for off in range(_PAGE_SIZE, total, _PAGE_SIZE)
        ))
        for page in remaining:
            extract_stubs(page)

        # Phase 2: fetch detail pages for descriptions
        done = 0

        async def fetch_detail(
            posting_id: str,
        ) -> tuple[str, str | None, Compensation | None]:
            nonlocal done
            url = f"{list_url}/{posting_id}"
            try:
                resp = await http.get(url)
                data = json.loads(resp.body)
                sections = (
                    data.get("jobAd", {}).get("sections", {})
                )
                parts = []
                for key in (
                    "jobDescription",
                    "qualifications",
                    "additionalInformation",
                ):
                    section = sections.get(key, {})
                    html = section.get("text", "")
                    if html:
                        parts.append(html_to_text(html))
                desc = "\n\n".join(parts)
                post_url = data.get("postingUrl")
                comp = _build_compensation(data.get("compensation"))
                return desc, post_url, comp
            except Exception:
                return "", None, None
            finally:
                done += 1
                if done % 200 == 0:
                    logger.info(
                        "company=%s details=%d/%d",
                        name,
                        done,
                        len(stubs),
                    )

        details = await asyncio.gather(*(
            fetch_detail(pid) for _, pid, _, _ in stubs
        ))
        logger.info(
            "company=%s details=%d done",
            name,
            len(details),
        )

        for (title, pid, location, released), (
            description,
            post_url,
            compensation,
        ) in zip(stubs, details, strict=True):
            if not post_url:
                post_url = (
                    f"https://jobs.smartrecruiters.com"
                    f"/{company}/{pid}"
                )
            posted = released[:10] if released else None
            h = job_hash(title, name, description)
            yield Job(
                hash=h,
                title=title,
                company=name,
                team=None,
                url=post_url,
                posted=posted,
                compensation=compensation,
                location=location,
                description=description,
                source=f"smartrecruiters:{company}",
            )

    return scrape
