import asyncio
import json
import logging
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper.html import html_to_text
from job_scraper.scraper.http import Http

logger = logging.getLogger(__name__)

_PAGE_SIZE = 20


def scrape_board(company: str, instance: str, site: str, *, name: str):
    """Return a scrape function for a Workday career site.

    Uses http.post() for listing pagination and http.get() for detail
    pages.  Both are cached and rate-limited.
    """
    base = f"https://{company}.{instance}.myworkdayjobs.com"
    jobs_url = f"{base}/wday/cxs/{company}/{site}/jobs"

    async def scrape(http: Http) -> AsyncIterator[Job]:
        # Phase 1: paginate listings via POST (cached)
        stubs: list[tuple[str, str, str | None]] = []

        try:
            first_body, scraped_at = await http.post(
                jobs_url,
                json={
                    "limit": _PAGE_SIZE,
                    "offset": 0,
                    "appliedFacets": {},
                    "searchText": "",
                },
            )
        except Exception:
            logger.warning(
                "company=%s offset=0 page_error=true",
                name,
            )
            return

        first = json.loads(first_body)
        total = first.get("total", 0)
        logger.info(
            "company=%s listings=%d",
            name,
            total,
        )

        def to_stub(
            posting: dict,
        ) -> tuple[str, str, str | None]:
            return (
                posting.get("title", ""),
                posting.get("externalPath", ""),
                posting.get("locationsText"),
            )

        for posting in first.get("jobPostings", []):
            stubs.append(to_stub(posting))

        # Fetch remaining pages concurrently
        async def fetch_page(offset: int) -> list[dict]:
            try:
                body, _ = await http.post(
                    jobs_url,
                    json={
                        "limit": _PAGE_SIZE,
                        "offset": offset,
                        "appliedFacets": {},
                        "searchText": "",
                    },
                )
                return json.loads(body).get("jobPostings", [])
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
            for posting in page:
                stubs.append(to_stub(posting))

        # Phase 2: fetch detail pages concurrently (cached)
        done = 0

        async def fetch_detail(
            ext_path: str,
        ) -> tuple[str, str | None, str | None]:
            nonlocal done
            url = f"{base}/wday/cxs/{company}/{site}{ext_path}"
            try:
                body, _ = await http.get(url)
                info = json.loads(body).get("jobPostingInfo", {})
                desc_html = info.get("jobDescription", "")
                desc = html_to_text(desc_html) if desc_html else ""
                start = info.get("startDate")
                posted = start[:10] if start else None
                # Build location from detail fields (more precise
                # than the listing's locationsText which may say
                # "2 Locations" etc.)
                locs: list[str] = []
                if primary := info.get("location"):
                    locs.append(primary)
                for extra in info.get("additionalLocations", []):
                    if extra and extra not in locs:
                        locs.append(extra)
                location = "; ".join(locs) if locs else None
                return desc, posted, location
            except Exception:
                logger.warning(
                    "company=%s detail_error=true path=%s",
                    name,
                    ext_path,
                )
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
            fetch_detail(ext_path) for _, ext_path, _ in stubs
        ))
        logger.info(
            "company=%s details=%d done",
            name,
            len(details),
        )

        for (title, ext_path, listing_loc), (
            description,
            posted,
            detail_loc,
        ) in zip(stubs, details, strict=True):
            post_url = f"{base}/{site}{ext_path}"
            h = job_hash(title, name, description)
            yield Job(
                hash=h,
                title=title,
                company=name,
                team=None,
                url=post_url,
                posted=posted,
                comp=None,
                location=detail_loc or listing_loc,
                description=description,
                source=f"workday:{company}",
                scraped_at=scraped_at,
            )

    return scrape
