import asyncio
import json
import re
from collections.abc import AsyncIterator

import httpx

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._html import html_to_text
from job_scraper.scraper._http import CachedHttpError, Http

_CAREERS_URL = "https://www.metacareers.com/jobs"
_GRAPHQL_URL = "https://www.metacareers.com/graphql"
_SEARCH_DOC_ID = "29615178951461218"
_DETAIL_BATCH = 50

_LSD_RE = re.compile(r'"LSD",\[\],\{"token":"([^"]+)"\}')
_JSONLD_RE = re.compile(
    r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _extract_lsd(html: str) -> str:
    m = _LSD_RE.search(html)
    if not m:
        msg = "could not extract LSD token from Meta careers page"
        raise ValueError(msg)
    return m.group(1)


def _parse_jsonld(html: str) -> dict:
    """Extract JSON-LD JobPosting data from a detail page."""
    m = _JSONLD_RE.search(html)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def _job_url(job_id: str) -> str:
    return f"https://www.metacareers.com/jobs/{job_id}/"


async def _fetch_detail(
    http: Http, job_id: str
) -> tuple[str, str | None]:
    """Return (description, posted_date) from a job detail page."""
    try:
        body, _ = await http.get(_job_url(job_id))
    except (httpx.HTTPStatusError, CachedHttpError):
        return "", None
    ld = _parse_jsonld(body)
    if not ld:
        return "", None

    parts: list[str] = []
    for field in ("description", "responsibilities", "qualifications"):
        val = ld.get(field, "")
        if val:
            parts.append(html_to_text(val))
    description = "\n\n".join(parts)

    posted = None
    date_posted = ld.get("datePosted", "")
    if date_posted:
        posted = date_posted[:10]

    return description, posted


async def scrape(http: Http) -> AsyncIterator[Job]:
    # Fetch initial page to set cookies and extract LSD token
    page_html, _ = await http.get(_CAREERS_URL)
    lsd = _extract_lsd(page_html)

    # Search for all jobs (results_per_page=null returns everything)
    search_vars = {
        "search_input": {
            "q": "",
            "divisions": [],
            "offices": [],
            "roles": [],
            "leadership_levels": [],
            "sub_teams": [],
            "teams": [],
            "is_leadership": False,
            "is_remote_only": False,
            "sort_by_new": False,
            "results_per_page": None,
            "saved_jobs": [],
            "saved_searches": [],
        }
    }
    form_data = {
        "lsd": lsd,
        "doc_id": _SEARCH_DOC_ID,
        "variables": json.dumps(search_vars),
    }
    body, scraped_at = await http.post_form(
        _GRAPHQL_URL,
        data=form_data,
        headers={"X-FB-LSD": lsd},
    )
    data = json.loads(body)
    results = data.get("data", {}).get(
        "job_search_with_featured_jobs", {}
    )
    all_jobs = results.get("all_jobs", [])

    # Fetch detail pages in batches to bound memory
    for i in range(0, len(all_jobs), _DETAIL_BATCH):
        batch = all_jobs[i : i + _DETAIL_BATCH]
        details = await asyncio.gather(
            *(
                _fetch_detail(http, entry.get("id", ""))
                for entry in batch
            )
        )
        for entry, (description, posted) in zip(
            batch, details, strict=True
        ):
            title = entry.get("title", "")
            locations = entry.get("locations")
            location = (
                "; ".join(locations) if locations else None
            )
            teams = entry.get("teams")
            team = "; ".join(teams) if teams else None
            job_id = entry.get("id", "")
            h = job_hash(title, "Meta", description)
            yield Job(
                hash=h,
                title=title,
                company="Meta",
                team=team,
                url=_job_url(job_id),
                posted=posted,
                comp=None,
                location=location,
                description=description,
                source="meta",
                scraped_at=scraped_at,
            )
