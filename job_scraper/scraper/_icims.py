import json
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._html import html_to_text
from job_scraper.scraper._http import Http


def scrape_board(domain: str, *, name: str):
    """Return a scrape function for an iCIMS Attract (Jibe) career site.

    ``domain`` is the careers hostname, e.g. ``"careers.rivian.com"``.
    The site must expose the ``/api/jobs`` JSON endpoint.
    """

    async def scrape(http: Http) -> AsyncIterator[Job]:
        base = f"https://{domain}/api/jobs"
        page = 1
        while True:
            url = f"{base}?page={page}"
            body, scraped_at = await http.get(url)
            data = json.loads(body)
            jobs = data.get("jobs", [])
            if not jobs:
                break
            for entry in jobs:
                d = entry.get("data", {})
                title = d.get("title", "")
                desc_html = d.get("description", "")
                quals_html = d.get("qualifications", "")
                resp_html = d.get("responsibilities", "")
                parts = [
                    html_to_text(h)
                    for h in (desc_html, resp_html, quals_html)
                    if h
                ]
                description = "\n\n".join(parts)

                location = (
                    d.get("full_location")
                    or d.get("short_location")
                    or d.get("location_name")
                )

                categories = d.get("categories", [])
                team = categories[0]["name"] if categories else None

                meta = d.get("meta_data", {})
                post_url = meta.get("canonical_url") or d.get(
                    "apply_url", ""
                )

                posted_date = d.get("posted_date")
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
                    source=f"icims:{domain}",
                    scraped_at=scraped_at,
                )
            page += 1

    return scrape
