from job_scraper.models import Compensation

_EN_DASH = "\u2013"


def _amount(n: int, use_k: bool) -> str:
    if use_k:
        return f"{(n + 500) // 1000:,}k"
    return f"{n:,}"


def _prefix(currency: str | None) -> str:
    if currency is None:
        return ""
    if currency == "USD":
        return "$"
    return f"{currency} "


_INTERVAL_SUFFIX = {
    "annual": "/yr",
    "hourly": "/hr",
    "monthly": "/mo",
    "weekly": "/wk",
}


def format_compensation(c: Compensation) -> str:
    prefix = _prefix(c.currency)
    suffix = _INTERVAL_SUFFIX.get(c.interval or "", "")

    bounds = [b for b in (c.min_amount, c.max_amount) if b is not None]
    use_k = bool(bounds) and all(b >= 1000 for b in bounds)

    if c.min_amount is not None and c.max_amount is not None:
        left = f"{prefix}{_amount(c.min_amount, use_k)}"
        right = f"{prefix if c.currency == 'USD' else ''}{_amount(c.max_amount, use_k)}"
        body = f"{left}{_EN_DASH}{right}"
    elif c.min_amount is not None:
        body = f"{prefix}{_amount(c.min_amount, use_k)}+"
    elif c.max_amount is not None:
        body = f"up to {prefix}{_amount(c.max_amount, use_k)}"
    else:
        body = ""

    extras = []
    if c.equity:
        extras.append("+equity")
    if c.bonus:
        extras.append("+bonus")
    extras_str = f" ({', '.join(extras)})" if extras else ""

    return f"{body}{suffix}{extras_str}"
