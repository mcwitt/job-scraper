import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._html import html_to_text
from job_scraper.scraper._http import Http

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
        now = datetime.now(UTC).isoformat()

        # Phase 1: paginate listings via POST (cached)
        stubs: list[tuple[str, str, str | None]] = []

        try:
            first_body = await http.post(
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
                "scraper=workday:%s company=%s offset=0"
                " page_error=true",
                company,
                name,
            )
            return

        first = json.loads(first_body)
        total = first.get("total", 0)
        logger.info(
            "scraper=workday:%s company=%s listings=%d",
            company,
            name,
            total,
        )

        for posting in first.get("jobPostings", []):
            stubs.append((
                posting.get("title", ""),
                posting.get("externalPath", ""),
                posting.get("locationsText"),
            ))

        # Fetch remaining pages concurrently
        async def fetch_page(offset: int) -> list[dict]:
            try:
                body = await http.post(
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
                    "scraper=workday:%s company=%s offset=%d"
                    " page_error=true",
                    company,
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
                stubs.append((
                    posting.get("title", ""),
                    posting.get("externalPath", ""),
                    posting.get("locationsText"),
                ))

        # Phase 2: fetch detail pages concurrently (cached)
        done = 0

        async def fetch_detail(ext_path: str) -> tuple[str, str | None]:
            nonlocal done
            url = f"{base}/wday/cxs/{company}/{site}{ext_path}"
            try:
                body = await http.get(url)
                info = json.loads(body).get("jobPostingInfo", {})
                desc_html = info.get("jobDescription", "")
                desc = html_to_text(desc_html) if desc_html else ""
                start = info.get("startDate")
                posted = start[:10] if start else None
                return desc, posted
            except Exception:
                return "", None
            finally:
                done += 1
                if done % 200 == 0:
                    logger.info(
                        "scraper=workday:%s company=%s"
                        " details=%d/%d",
                        company,
                        name,
                        done,
                        len(stubs),
                    )

        details = await asyncio.gather(*(
            fetch_detail(ext_path) for _, ext_path, _ in stubs
        ))
        logger.info(
            "scraper=workday:%s company=%s details=%d done",
            company,
            name,
            len(details),
        )

        for (title, ext_path, location), (description, posted) in zip(
            stubs, details, strict=True
        ):
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
                location=location,
                description=description,
                source=f"workday:{company}",
                scraped_at=now,
            )

    return scrape
