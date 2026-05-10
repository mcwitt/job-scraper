from collections.abc import AsyncIterator
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as ET

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper.html import html_to_text
from job_scraper.scraper.http import Http

_NS = {"job": "https://recruiterbox.com/rss/job/"}


def _location(item: Element) -> str | None:
    parts = []
    for tag in ("locationCity", "locationState", "locationCountry"):
        el = item.find(f"job:{tag}", _NS)
        if el is not None and el.text:
            parts.append(el.text)
    return ", ".join(parts) if parts else None


def scrape_board(slug: str, *, name: str):
    """Return a scrape function for a Trakstar Hire board."""

    async def scrape(http: Http) -> AsyncIterator[Job]:
        url = (
            f"https://{slug}.hire.trakstar.com"
            f"/jobfeeds/{slug}"
        )
        resp = await http.get(url)
        root = ET.fromstring(resp.body)
        for item in root.iter("item"):
            title_el = item.find("title")
            title = (title_el.text or "") if title_el is not None else ""

            link_el = item.find("link")
            post_url = (
                link_el.text.replace("http://", "https://").lower()
                if link_el is not None and link_el.text
                else ""
            )

            desc_el = item.find("description")
            raw_desc = desc_el.text if desc_el is not None else ""
            description = html_to_text(raw_desc) if raw_desc else ""

            pub_el = item.find("pubDate")
            posted = None
            if pub_el is not None and pub_el.text:
                # RFC 2822: "Thu, 26 Mar 2026 00:00:00 +0530"
                parts = pub_el.text.split()
                if len(parts) >= 4:
                    day, mon, year = parts[1], parts[2], parts[3]
                    mon_num = {
                        "Jan": "01", "Feb": "02", "Mar": "03",
                        "Apr": "04", "May": "05", "Jun": "06",
                        "Jul": "07", "Aug": "08", "Sep": "09",
                        "Oct": "10", "Nov": "11", "Dec": "12",
                    }.get(mon)
                    if mon_num:
                        posted = f"{year}-{mon_num}-{day.zfill(2)}"

            team_el = item.find("job:team", _NS)
            team = (
                team_el.text if team_el is not None and team_el.text
                else None
            )

            location = _location(item)

            h = job_hash(title, name, description)
            yield Job(
                hash=h,
                title=title,
                company=name,
                team=team,
                url=post_url,
                posted=posted,
                compensation=None,
                location=location,
                description=description,
                source=f"trakstar:{slug}",
            )

    return scrape
