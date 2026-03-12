import asyncio
import json
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._html import html_to_text
from job_scraper.scraper._http import Http

_PAGE_SIZE = 500


def scrape_board(domain: str, *, name: str):
    """Return a scrape function for a Phenom People career site.

    ``domain`` is the careers hostname, e.g. ``"careers.snowflake.com"``.
    The site must expose the ``/widgets`` POST endpoint.
    """

    async def scrape(http: Http) -> AsyncIterator[Job]:
        widgets_url = f"https://{domain}/widgets"

        # Phase 1: paginate job listings
        stubs: list[dict] = []
        offset = 0
        while True:
            payload = {
                "lang": "en_us",
                "deviceType": "desktop",
                "country": "us",
                "pageName": "search-results",
                "ddoKey": "refineSearch",
                "from": offset,
                "size": _PAGE_SIZE,
                "jobs": True,
                "counts": False,
                "all_fields": [
                    "category",
                    "country",
                    "state",
                    "city",
                    "type",
                    "siteType",
                ],
                "keywords": "",
            }
            body, scraped_at = await http.post(widgets_url, json=payload)
            data = json.loads(body)
            refine = data.get("refineSearch", {})
            jobs = refine.get("data", {}).get("jobs", [])
            stubs.extend(jobs)
            total = refine.get("totalHits", 0)
            if not jobs or offset + len(jobs) >= total:
                break
            offset += len(jobs)

        # Phase 2: fetch full descriptions concurrently
        async def fetch_detail(seq_no: str) -> str:
            payload = {
                "lang": "en_us",
                "deviceType": "desktop",
                "country": "us",
                "pageName": "job",
                "ddoKey": "jobDetail",
                "jobSeqNo": seq_no,
            }
            body, _ = await http.post(widgets_url, json=payload)
            detail = json.loads(body)
            desc_html = (
                detail.get("jobDetail", {})
                .get("data", {})
                .get("job", {})
                .get("description", "")
            )
            return html_to_text(desc_html) if desc_html else ""

        descriptions = await asyncio.gather(
            *(fetch_detail(s.get("jobSeqNo", "")) for s in stubs)
        )

        for stub, description in zip(stubs, descriptions, strict=True):
            title = stub.get("title", "")
            location = stub.get("location") or stub.get("city")
            team = stub.get("category") or stub.get("department")

            seq_no = stub.get("jobSeqNo", "")
            post_url = f"https://{domain}/us/en/job/{seq_no}"

            posted_date = stub.get("postedDate")
            posted = posted_date[:10] if posted_date else None

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
                source=f"phenom:{domain}",
                scraped_at=scraped_at,
            )

    return scrape
