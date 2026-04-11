import json
import re
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper.html import html_to_text
from job_scraper.scraper.http import Http


def _parse_rsc(body: str) -> tuple[list[dict], dict[str, str]]:
    """Parse Next.js RSC flight data from a Kula careers page.

    Returns (jobs_list, {ref: html_content}).
    """
    # Extract all push payloads and concatenate into one stream
    pushes = re.findall(
        r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', body
    )
    stream = "".join(
        p.encode().decode("unicode_escape") for p in pushes
    )

    # Extract jobs array from the jobs-component RSC payload
    jobs_start = stream.find('"jobs":[{')
    if jobs_start < 0:
        return [], {}
    arr_start = jobs_start + len('"jobs":')
    decoder = json.JSONDecoder()
    jobs: list[dict] = decoder.raw_decode(stream, arr_start)[0]

    # Resolve $-refs: RSC text chunks use REF:TLEN,CONTENT
    # where REF is hex id, LEN is hex byte-length of CONTENT
    refs: dict[str, str] = {}
    stream_bytes = stream.encode("utf-8")
    for m in re.finditer(r"([0-9a-f]+):T([0-9a-f]+),", stream):
        ref = m.group(1)
        byte_len = int(m.group(2), 16)
        byte_offset = len(stream[: m.end()].encode("utf-8"))
        refs[f"${ref}"] = stream_bytes[
            byte_offset : byte_offset + byte_len
        ].decode("utf-8")

    return jobs, refs


def scrape_board(token: str, *, name: str):
    """Return a scrape function for a Kula career site.

    ``token`` is the slug from ``careers.kula.ai/{slug}``.
    """

    async def scrape(http: Http) -> AsyncIterator[Job]:
        url = f"https://careers.kula.ai/{token}"
        resp = await http.get(url)
        jobs, refs = _parse_rsc(resp.body)

        for posting in jobs:
            if not posting.get("listed", True):
                continue

            title = posting.get("title", "").strip()
            ats = posting.get("ats_job", {})

            desc_ref = ats.get("job_description", "")
            desc_html = refs.get(desc_ref, "")
            description = html_to_text(desc_html) if desc_html else ""

            dept = ats.get("ats_department") or {}
            team = dept.get("name", "").strip() or None

            offices = ats.get("offices") or []
            location = offices[0].get("location") if offices else None

            job_id = posting.get("id", "")
            post_url = f"https://careers.kula.ai/{token}/jobs/{job_id}"

            workplace = ats.get("workplace")
            emp_type = ats.get("employment_type")
            meta_parts = [
                p
                for p in [workplace, emp_type]
                if p
            ]
            meta = ", ".join(meta_parts)
            if meta and location:
                location = f"{location} ({meta})"
            elif meta:
                location = meta

            h = job_hash(title, name, description)
            yield Job(
                hash=h,
                title=title,
                company=name,
                team=team,
                url=post_url,
                posted=None,
                comp=None,
                location=location,
                description=description,
                source=f"kula:{token}",
                scraped_at=resp.fetched_at,
            )

    return scrape
