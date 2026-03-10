import re
from collections.abc import AsyncIterator
from datetime import datetime

from bs4 import BeautifulSoup

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._http import Http

_LIST_URL = (
    "https://lbl.referrals.selectminds.com"
    "/page/lawrence-berkeley-national-laboratory-jobs-702"
)
_COMPANY = "Lawrence Berkeley National Lab"
_DATE_RE = re.compile(r"[A-Z][a-z]{2} \d{1,2}, \d{4}")


def _parse_date(raw: str) -> str | None:
    """Parse 'Mar 02, 2026' style dates; skip relative ones."""
    m = _DATE_RE.search(raw)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(0), "%b %d, %Y")
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d")


async def scrape(http: Http) -> AsyncIterator[Job]:
    html, scraped_at = await http.get(_LIST_URL)
    soup = BeautifulSoup(html, "lxml")

    rows = soup.select("[id^='job_list_']")

    for row in rows:
        link = row.select_one(".job_link")
        if not link:
            continue
        title = link.get_text(strip=True)
        url = str(link.get("href", ""))

        loc_el = row.select_one(".location")
        location = (
            loc_el.get_text(strip=True).lstrip("\U0001f50d").strip()
            if loc_el
            else None
        )

        date_el = row.select_one(".job_post_date .field_value")
        posted = _parse_date(date_el.get_text(strip=True)) if date_el else None

        # Fetch detail page for full description
        detail_html, _ = await http.get(url)
        detail_soup = BeautifulSoup(detail_html, "lxml")
        desc_el = detail_soup.select_one(".job_description")
        description = (
            desc_el.get_text(separator="\n", strip=True) if desc_el else ""
        )

        h = job_hash(title, _COMPANY, description)
        yield Job(
            hash=h,
            title=title,
            company=_COMPANY,
            team=None,
            url=url,
            posted=posted,
            comp=None,
            location=location,
            description=description,
            source="lbnl",
            scraped_at=scraped_at,
        )
