"""Extract structured compensation from plaintext job descriptions.

Used only as a fallback for ATS platforms that don't expose pay data in
a structured API field (Google, Workday, iCIMS, Phenom, Netflix). The
patterns target US pay-transparency boilerplate, so currency defaults
to USD and we assume annual pay unless an explicit per-hour/per-week
marker is present.
"""

import re

from job_scraper.models import Compensation, Interval

_AMOUNT = r"\$\s*([\d,]+(?:\.\d+)?)\s*([KkMm]?)"
_SEP = r"\s*(?:-|\u2013|to)\s*"

# Anchored context phrases. Order doesn't matter — first match wins.
# Each pattern uses non-capturing context groups so groups 1/2 are
# always (lo_num, lo_unit) and groups 3/4 are (hi_num, hi_unit).
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "salary range ...", "pay range ...", "base salary range ...",
    # "U.S. base pay range ...", "annual salary range ..."
    re.compile(
        r"(?:annual|base|hourly|u\.?s\.?|usd)?\s*"
        r"(?:salary|pay|compensation|wage)\s+range"
        rf"[^$\n]{{0,120}}{_AMOUNT}\$?{_SEP}{_AMOUNT}",
        re.IGNORECASE,
    ),
    # "range for this role is $X - $Y" (Netflix, Phenom, iCIMS variants)
    re.compile(
        r"range\s+for\s+this\s+role"
        rf"[^$\n]{{0,120}}{_AMOUNT}\$?{_SEP}{_AMOUNT}",
        re.IGNORECASE,
    ),
    # "Compensation: $X - $Y" (Workable agency listings)
    re.compile(
        rf"compensation\s*:\s*{_AMOUNT}\$?{_SEP}{_AMOUNT}",
        re.IGNORECASE,
    ),
)

_INTERVAL_KEYWORDS: tuple[tuple[str, Interval], ...] = (
    # Hourly markers checked first — "an hour" trumps a stray "annual"
    # earlier in the same sentence.
    ("an hour", "hourly"),
    ("per hour", "hourly"),
    ("/hr", "hourly"),
    ("/hour", "hourly"),
    ("hourly", "hourly"),
    ("per week", "weekly"),
    ("/wk", "weekly"),
    ("weekly", "weekly"),
    ("per month", "monthly"),
    ("/mo", "monthly"),
    ("monthly", "monthly"),
    ("per year", "annual"),
    ("/yr", "annual"),
    ("/year", "annual"),
    ("a year", "annual"),
    ("annual", "annual"),
    ("annually", "annual"),
)


def _to_int(num: str, unit: str) -> int:
    n = float(num.replace(",", ""))
    u = unit.lower()
    if u == "k":
        n *= 1000
    elif u == "m":
        n *= 1_000_000
    return int(n)


def _infer_interval(window: str, min_amount: int) -> Interval:
    w = window.lower()
    for kw, interval in _INTERVAL_KEYWORDS:
        if kw in w:
            return interval
    # Fallback by magnitude: <$1k almost always means hourly wage.
    return "hourly" if min_amount < 1000 else "annual"


def extract_from_text(text: str) -> Compensation | None:
    """Return a Compensation parsed from anchored pay-transparency text.

    Anchored means the dollar amounts must be preceded by a phrase like
    "salary range", "pay range", "compensation:", or "range for this
    role" — not just any "$X-$Y" pair in the description.
    """
    if not text:
        return None
    for pat in _PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        lo = _to_int(m.group(1), m.group(2))
        hi = _to_int(m.group(3), m.group(4))
        # Sanity: both bounds positive, hi >= lo, and within plausible
        # human-pay range (>$1/hr, <$10M/yr).
        if lo <= 0 or hi <= 0 or hi < lo or hi > 10_000_000:
            continue
        window = text[max(0, m.start() - 60) : m.end() + 60]
        interval = _infer_interval(window, lo)
        # Reject mismatches: annual bounds shouldn't be in hourly-wage
        # magnitude. Often signals a typo in the source ("$100,00")
        # or a "$X.XX per hour" line that we still mis-classified.
        if interval == "annual" and hi < 10_000:
            continue
        # Magnitude override: no real hourly wage is >$1k/hr. Sources
        # like Rivian print "Salary Range/Hourly Rate range" on annual
        # numbers, which tricks the keyword-based inference.
        if interval == "hourly" and lo > 1000:
            interval = "annual"
        return Compensation(
            min_amount=lo,
            max_amount=hi,
            currency="USD",
            interval=interval,
        )
    return None
