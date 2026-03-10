import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._http import Http

logger = logging.getLogger(__name__)

_CAREERS_URL = "https://kronosai.co/careers"
_CHUNK_RE = re.compile(
    r'/_next/static/chunks/app/careers/page-[0-9a-f]+\.js'
)


def _extract_jobs_js(chunk: str) -> list[dict[str, str]]:
    """Parse job objects from the embedded JS array in the careers chunk."""
    idx = chunk.find('m=[{id:')
    if idx == -1:
        return []

    start = idx + 2  # skip 'm='
    depth = 0
    end = start
    for i in range(start, len(chunk)):
        c = chunk[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    raw = chunk[start:end]

    # Split into top-level job objects using a string-aware state machine
    jobs_raw: list[str] = []
    depth = 0
    obj_start = 0
    in_string = False
    escape = False

    for i, c in enumerate(raw):
        if escape:
            escape = False
            continue
        if c == "\\" and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c in "[{":
            depth += 1
            if depth == 2 and c == "{":
                obj_start = i
        elif c in "]}":
            depth -= 1
            if depth == 1 and c == "}":
                jobs_raw.append(raw[obj_start : i + 1])

    results: list[dict[str, str]] = []
    for jr in jobs_raw:
        jid_m = re.search(r'id:"([^"]+)"', jr)
        title_m = re.search(r'title:"([^"]+)"', jr)
        loc_m = re.search(r'location:"([^"]*)"', jr)
        if not jid_m or not title_m:
            continue

        # Description: between location/title and responsibilities
        before_resp = jr.split(",responsibilities:")[0]
        desc_m = re.search(
            r'description:"((?:[^"\\]|\\.)*)"', before_resp
        )
        desc = desc_m.group(1) if desc_m else ""

        # Responsibilities array
        resps: list[str] = []
        if ",responsibilities:" in jr and "],requirements:" in jr:
            resp_part = jr.split(",responsibilities:")[1].split(
                "],requirements:"
            )[0]
            resps = re.findall(r'"((?:[^"\\]|\\.)*)"', resp_part)

        # Requirements descriptions
        req_descs: list[str] = []
        if "],requirements:" in jr:
            req_part = jr.split("],requirements:")[1]
            req_descs = re.findall(
                r'description:"((?:[^"\\]|\\.)*)"', req_part
            )

        full = desc
        if resps:
            full += "\n\nResponsibilities:\n" + "\n".join(
                f"- {r}" for r in resps
            )
        if req_descs:
            full += "\n\nRequirements:\n" + "\n".join(
                f"- {r}" for r in req_descs
            )

        results.append({
            "id": jid_m.group(1),
            "title": title_m.group(1),
            "location": loc_m.group(1) if loc_m else "",
            "description": full,
        })

    return results


async def scrape(http: Http) -> AsyncIterator[Job]:
    now = datetime.now(UTC).isoformat()

    # Step 1: fetch careers page to find the JS chunk URL
    html = await http.get(_CAREERS_URL)
    chunk_match = _CHUNK_RE.search(html)
    if not chunk_match:
        logger.warning(
            "scraper=kronosai msg='could not find careers JS chunk'"
        )
        return

    chunk_url = f"https://kronosai.co{chunk_match.group(0)}"

    # Step 2: fetch and parse the JS chunk
    chunk_js = await http.get(chunk_url)
    jobs = _extract_jobs_js(chunk_js)
    for job in jobs:
        title = job["title"]
        description = job["description"]
        location = job["location"] or None
        url = f"{_CAREERS_URL}#{job['id']}"
        h = job_hash(title, "KronosAI", description)
        yield Job(
            hash=h,
            title=title,
            company="KronosAI",
            team=None,
            url=url,
            posted=None,
            comp=None,
            location=location,
            description=description,
            source="kronosai",
            scraped_at=now,
        )
