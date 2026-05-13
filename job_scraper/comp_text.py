"""Extract structured compensation from plaintext job descriptions.

Used only as a fallback for ATS platforms that don't expose pay data in
a structured API field (Google, Workday, iCIMS, Phenom, Netflix). The
patterns target US pay-transparency boilerplate, so currency defaults
to USD and we assume annual pay unless an explicit per-hour/per-week
marker is present.
"""

import re

from job_scraper.models import Compensation, Interval

# Lookbehind on `\$` matches `ashby.py`'s _USD: keeps "CA$" / "AU$" /
# similar non-USD prefixes from being parsed as USD.
_AMOUNT = r"(?<![A-Za-z])\$\s*([\d,]+(?:\.\d+)?)\s*([KkMm]?)"
_SEP = r"\s*(?:-|\u2013|to)\s*"

# Single combined pattern: one scan over the description instead of
# three. Groups 1/2 are (lo_num, lo_unit) and 3/4 are (hi_num, hi_unit).
_PATTERN = re.compile(
    r"(?:"
    r"(?:annual|base|hourly|u\.?s\.?|usd)?\s*"
    r"(?:salary|pay|compensation|wage)\s+range"
    r"|range\s+for\s+this\s+role"
    r"|compensation\s*:"
    r")"
    rf"[^$\n]{{0,120}}{_AMOUNT}\$?{_SEP}{_AMOUNT}",
    re.IGNORECASE,
)

# Hourly markers checked first — "an hour" trumps a stray "annual"
# earlier in the same sentence.
_INTERVAL_KEYWORDS: tuple[tuple[str, Interval], ...] = (
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
    if not text or "$" not in text:
        return None
    m = _PATTERN.search(text)
    if m is None:
        return None
    lo = _to_int(m.group(1), m.group(2))
    hi = _to_int(m.group(3), m.group(4))
    if lo <= 0 or hi <= 0 or hi < lo or hi > 10_000_000:
        return None
    window = text[max(0, m.start() - 60) : m.end() + 60]
    interval = _infer_interval(window, lo)
    # Annual amounts < $10k usually mean a typo in the source ("$100,00")
    # or a per-hour line we mis-inferred.
    if interval == "annual" and hi < 10_000:
        return None
    # No real hourly wage is >$1k/hr. Some boilerplate prints "Hourly
    # Rate" alongside annual amounts; override rather than reject since
    # the amounts are still meaningful.
    if interval == "hourly" and lo > 1000:
        interval = "annual"
    return Compensation(
        min_amount=lo,
        max_amount=hi,
        currency="USD",
        interval=interval,
    )
