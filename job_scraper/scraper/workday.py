import asyncio
import json
import logging
from collections.abc import AsyncIterator

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper.html import html_to_text
from job_scraper.scraper.http import Http

logger = logging.getLogger(__name__)

_PAGE_SIZE = 20
_API_CAP = 2000


def scrape_board(company: str, instance: str, site: str, *, name: str):
    """Return a scrape function for a Workday career site.

    Uses http.post() for listing pagination and http.get() for detail
    pages.  Both are cached and rate-limited.

    Workday caps unfaceted queries at 2000 results.  When a board
    exceeds this, we partition by facet dimensions whose buckets
    all fit under the cap, paginate each bucket, and dedupe.
    """
    base = f"https://{company}.{instance}.myworkdayjobs.com"
    jobs_url = f"{base}/wday/cxs/{company}/{site}/jobs"

    Stub = tuple[str, str, str | None]  # (title, externalPath, loc)

    async def _post(
        http: Http,
        offset: int,
        facets: dict[str, list[str]],
    ) -> tuple[dict, str, list[dict]]:
        """POST the jobs endpoint.

        Returns (body, fetched_at, postings).
        """
        try:
            resp = await http.post(
                jobs_url,
                json={
                    "limit": _PAGE_SIZE,
                    "offset": offset,
                    "appliedFacets": facets,
                    "searchText": "",
                },
            )
            body = json.loads(resp.body)
            return body, resp.fetched_at, body.get(
                "jobPostings", []
            )
        except Exception:
            logger.warning(
                "company=%s offset=%d page_error=true",
                name,
                offset,
            )
            return {}, "", []

    def _to_stub(posting: dict) -> Stub:
        return (
            posting.get("title", ""),
            posting.get("externalPath", ""),
            posting.get("locationsText"),
        )

    async def _paginate(
        http: Http,
        facets: dict[str, list[str]],
    ) -> list[Stub]:
        """Fully paginate a query that fits under the API cap."""
        body, _, first_postings = await _post(http, 0, facets)
        total = body.get("total", 0)
        stubs = [_to_stub(p) for p in first_postings]

        if total > _PAGE_SIZE:
            pages = await asyncio.gather(*(
                _post(http, off, facets)
                for off in range(_PAGE_SIZE, total, _PAGE_SIZE)
            ))
            for _, _, postings in pages:
                stubs.extend(_to_stub(p) for p in postings)

        return stubs

    def _flat_facets(facets_list: list[dict]) -> list[dict]:
        """Flatten facets (including nested locationMainGroup)."""
        out: list[dict] = []
        for fg in facets_list:
            if fg.get("values"):
                out.append(fg)
            for child in fg.get("facets", []):
                if child.get("values"):
                    out.append(child)
        return out

    async def _collect_all(
        http: Http,
        facets: dict[str, list[str]],
    ) -> list[Stub]:
        """Recursively collect stubs, subdividing by facets when
        results hit the API cap."""
        body, _, _ = await _post(http, 0, facets)
        total = body.get("total", 0)

        if total < _API_CAP:
            return await _paginate(http, facets)

        # Over the cap — pick a facet dimension to subdivide by
        resp_facets = _flat_facets(body.get("facets", []))
        used = set(facets)
        candidates = [
            f
            for f in resp_facets
            if f.get("facetParameter", "") not in used
            and f.get("values")
        ]

        if not candidates:
            logger.warning(
                "company=%s total=%d exceeds cap, "
                "no more facets to partition by",
                name,
                total,
            )
            return await _paginate(http, facets)

        # Prefer the dimension with the smallest max bucket
        candidates.sort(
            key=lambda f: max(
                v.get("count", 0) for v in f["values"]
            )
        )
        chosen = candidates[0]
        param = chosen["facetParameter"]
        buckets = [
            v for v in chosen["values"] if v.get("count", 0) > 0
        ]
        logger.info(
            "company=%s partitioning by %s (%d buckets)",
            name,
            param,
            len(buckets),
        )

        results = await asyncio.gather(*(
            _collect_all(http, {**facets, param: [v["id"]]})
            for v in buckets
        ))

        all_stubs: list[Stub] = []
        seen: set[str] = set()
        for bucket_stubs in results:
            for stub in bucket_stubs:
                path = stub[1]
                if path not in seen:
                    seen.add(path)
                    all_stubs.append(stub)
        return all_stubs

    async def scrape(http: Http) -> AsyncIterator[Job]:
        body, scraped_at_ts, _ = await _post(http, 0, {})
        total = body.get("total", 0)
        logger.info("company=%s listings=%d", name, total)

        stubs = await _collect_all(http, {})
        if total >= _API_CAP:
            logger.info(
                "company=%s faceted_total=%d",
                name,
                len(stubs),
            )

        # Phase 2: fetch detail pages concurrently (cached)
        done = 0

        async def fetch_detail(
            ext_path: str,
        ) -> tuple[str, str | None, str | None]:
            nonlocal done
            url = (
                f"{base}/wday/cxs/{company}/{site}{ext_path}"
            )
            try:
                resp = await http.get(url)
                info = json.loads(resp.body).get(
                    "jobPostingInfo", {}
                )
                desc_html = info.get("jobDescription", "")
                desc = (
                    html_to_text(desc_html) if desc_html else ""
                )
                start = info.get("startDate")
                posted = start[:10] if start else None
                locs: list[str] = []
                if primary := info.get("location"):
                    locs.append(primary)
                for extra in info.get(
                    "additionalLocations", []
                ):
                    if extra and extra not in locs:
                        locs.append(extra)
                location = (
                    "; ".join(locs) if locs else None
                )
                return desc, posted, location
            except Exception:
                logger.warning(
                    "company=%s detail_error=true path=%s",
                    name,
                    ext_path,
                )
                return "", None, None
            finally:
                done += 1
                if done % 200 == 0:
                    logger.info(
                        "company=%s details=%d/%d",
                        name,
                        done,
                        len(stubs),
                    )

        details = await asyncio.gather(*(
            fetch_detail(ext_path)
            for _, ext_path, _ in stubs
        ))
        logger.info(
            "company=%s details=%d done",
            name,
            len(details),
        )

        for (title, ext_path, listing_loc), (
            description,
            posted,
            detail_loc,
        ) in zip(stubs, details, strict=True):
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
                location=detail_loc or listing_loc,
                description=description,
                source=f"workday:{company}",
                scraped_at=scraped_at_ts,
            )

    return scrape
