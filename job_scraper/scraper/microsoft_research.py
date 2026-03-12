import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._html import html_to_text
from job_scraper.scraper._http import Http

logger = logging.getLogger(__name__)

_BASE = "https://www.microsoft.com/en-us/research/careers/open-positions/"
_CARD_RE = re.compile(
    r"<article\s+class=\"card card--job-opportunity\">.*?</article>",
    re.DOTALL,
)
_TITLE_RE = re.compile(r'data-bi-cN="([^"]+)"')
_URL_RE = re.compile(
    r'<a\s+href="(https://apply\.careers\.microsoft\.com/[^"]+)"'
)
_DATE_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')
_LOCATION_RE = re.compile(
    r'card__locations">\s*<span>[^<]*</span>:\s*(.*?)\s*</div>',
    re.DOTALL,
)
_AREA_RE = re.compile(
    r'card__research-area">\s*<span>[^<]*</span>:\s*(.*?)\s*</div>',
    re.DOTALL,
)
_MAX_PAGE_RE = re.compile(r'data-page="(\d+)"')
_TOTAL_RE = re.compile(r"(\d+)\s+results")
_JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _parse_cards(
    body: str,
) -> list[tuple[str, str, str | None, str | None, str | None]]:
    """Extract (title, url, posted, location, area) from listing HTML."""
    cards = _CARD_RE.findall(body)
    results = []
    for card in cards:
        title_m = _TITLE_RE.search(card)
        url_m = _URL_RE.search(card)
        if not title_m or not url_m:
            continue
        title = title_m.group(1)
        url = url_m.group(1)
        date_m = _DATE_RE.search(card)
        posted = date_m.group(1) if date_m else None
        loc_m = _LOCATION_RE.search(card)
        location = loc_m.group(1).strip() if loc_m else None
        area_m = _AREA_RE.search(card)
        area = area_m.group(1).strip() if area_m else None
        results.append((title, url, posted, location, area))
    return results


def _max_page(body: str) -> int:
    """Extract the highest page number from pagination links."""
    pages = [int(m) for m in _MAX_PAGE_RE.findall(body)]
    return max(pages) if pages else 0


async def scrape(http: Http) -> AsyncIterator[Job]:
    # Fetch first page to get total pages
    body, scraped_at = await http.get(_BASE)
    total_m = _TOTAL_RE.search(body)
    total = int(total_m.group(1)) if total_m else 0
    last_page = _max_page(body)
    logger.info(
        "scraper=microsoft_research listings=%d pages=%d",
        total,
        last_page + 1,
    )

    stubs: list[tuple[str, str, str | None, str | None, str | None]] = []
    stubs.extend(_parse_cards(body))

    # Fetch remaining listing pages
    for pg in range(1, last_page + 1):
        try:
            pg_body, _ = await http.get(f"{_BASE}?pg={pg}")
            stubs.extend(_parse_cards(pg_body))
        except Exception:
            logger.warning(
                "scraper=microsoft_research page=%d error=true", pg
            )

    logger.info(
        "scraper=microsoft_research stubs=%d", len(stubs)
    )

    # Fetch detail pages concurrently for full descriptions
    done = 0

    async def fetch_detail(
        url: str,
    ) -> tuple[str, str | None]:
        nonlocal done
        try:
            detail_body, _ = await http.get(url)
            for m in _JSONLD_RE.finditer(detail_body):
                try:
                    data = json.loads(m.group(1))
                except (json.JSONDecodeError, ValueError):
                    continue
                if data.get("@type") == "JobPosting":
                    raw = data.get("description", "")
                    desc = html_to_text(raw) if raw else ""
                    dp = data.get("datePosted")
                    return desc, dp[:10] if dp else None
        except Exception:
            logger.debug("scraper=microsoft_research url=%s error", url)
        finally:
            done += 1
            if done % 50 == 0:
                logger.info(
                    "scraper=microsoft_research details=%d/%d",
                    done,
                    len(stubs),
                )
        return "", None

    details = await asyncio.gather(*(
        fetch_detail(url) for _, url, *_ in stubs
    ))
    logger.info(
        "scraper=microsoft_research details=%d done",
        len(details),
    )

    for (title, url, posted, location, area), (desc, detail_posted) in zip(
        stubs, details, strict=True
    ):
        if not posted and detail_posted:
            posted = detail_posted
        team = f"Research: {area}" if area else "Research"
        h = job_hash(title, "Microsoft Research", desc)
        yield Job(
            hash=h,
            title=title,
            company="Microsoft Research",
            team=team,
            url=url,
            posted=posted,
            comp=None,
            location=location,
            description=desc,
            source="microsoft_research",
            scraped_at=scraped_at,
        )
