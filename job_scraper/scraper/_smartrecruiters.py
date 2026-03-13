import asyncio
import json
import logging
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._html import html_to_text
from job_scraper.scraper._http import Http

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100


def scrape_board(company: str, *, name: str):
    """Return a scrape function for a SmartRecruiters career site."""
    base = "https://api.smartrecruiters.com/v1/companies"
    list_url = f"{base}/{company}/postings"

    async def scrape(http: Http) -> AsyncIterator[Job]:
        # Phase 1: paginate listings
        stubs: list[tuple[str, str, str | None, str | None]] = []

        try:
            first_body, scraped_at = await http.get(
                f"{list_url}?limit={_PAGE_SIZE}&offset=0"
            )
        except Exception:
            logger.warning(
                "scraper=smartrecruiters:%s company=%s"
                " offset=0 page_error=true",
                company,
                name,
            )
            return

        first = json.loads(first_body)
        total = first.get("totalFound", 0)
        logger.info(
            "scraper=smartrecruiters:%s company=%s listings=%d",
            company,
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
                body, _ = await http.get(
                    f"{list_url}?limit={_PAGE_SIZE}"
                    f"&offset={offset}"
                )
                return json.loads(body).get("content", [])
            except Exception:
                logger.warning(
                    "scraper=smartrecruiters:%s company=%s"
                    " offset=%d page_error=true",
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
            extract_stubs(page)

        # Phase 2: fetch detail pages for descriptions
        done = 0

        async def fetch_detail(
            posting_id: str,
        ) -> tuple[str, str | None]:
            nonlocal done
            url = f"{list_url}/{posting_id}"
            try:
                body, _ = await http.get(url)
                data = json.loads(body)
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
                return desc, post_url
            except Exception:
                return "", None
            finally:
                done += 1
                if done % 200 == 0:
                    logger.info(
                        "scraper=smartrecruiters:%s company=%s"
                        " details=%d/%d",
                        company,
                        name,
                        done,
                        len(stubs),
                    )

        details = await asyncio.gather(*(
            fetch_detail(pid) for _, pid, _, _ in stubs
        ))
        logger.info(
            "scraper=smartrecruiters:%s company=%s"
            " details=%d done",
            company,
            name,
            len(details),
        )

        for (title, pid, location, released), (
            description,
            post_url,
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
                comp=None,
                location=location,
                description=description,
                source=f"smartrecruiters:{company}",
                scraped_at=scraped_at,
            )

    return scrape
