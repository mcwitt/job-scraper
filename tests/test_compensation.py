"""Tests for Compensation dataclass and format_compensation."""

import dataclasses

import pytest

from job_scraper.models import Compensation


def test_compensation_is_frozen():
    c = Compensation(min_amount=100000, max_amount=200000, currency="USD",
                     interval="annual")
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.min_amount = 0  # type: ignore[misc]


def test_compensation_defaults():
    c = Compensation(min_amount=100000, max_amount=None, currency=None,
                     interval=None)
    assert c.equity is False
    assert c.bonus is False


from job_scraper.comp import format_compensation


@pytest.mark.parametrize("c, expected", [
    # USD, K-notation, intervals
    (Compensation(165200, 223600, "USD", "annual"), "$165k\u2013$224k/yr"),
    (Compensation(28, 32, "USD", "hourly"), "$28\u2013$32/hr"),
    (Compensation(8000, 10000, "USD", "monthly"), "$8k\u2013$10k/mo"),
    (Compensation(2000, 2500, "USD", "weekly"), "$2k\u2013$3k/wk"),
    # Non-USD currency code prefix
    (Compensation(92000, 115000, "EUR", "annual"), "EUR 92k\u2013115k/yr"),
    (Compensation(1050000, 1500000, "INR", None), "INR 1,050k\u20131,500k"),
    # Interval=None → no suffix
    (Compensation(165200, 223600, "USD", None), "$165k\u2013$224k"),
    # Single-bound min
    (Compensation(165000, None, "USD", "annual"), "$165k+/yr"),
    # Single-bound max
    (Compensation(None, 200000, "USD", "annual"), "up to $200k/yr"),
    # K-threshold: under 1000 stays raw
    (Compensation(43, 50, "USD", "hourly"), "$43\u2013$50/hr"),
    # Mixed: one side under threshold, other above → raw
    (Compensation(900, 1200, "USD", "hourly"), "$900\u2013$1,200/hr"),
    # Equity / bonus suffixes
    (Compensation(100000, 150000, "USD", "annual", equity=True),
     "$100k\u2013$150k/yr (+equity)"),
    (Compensation(100000, 150000, "USD", "annual", bonus=True),
     "$100k\u2013$150k/yr (+bonus)"),
    (Compensation(100000, 150000, "USD", "annual", equity=True, bonus=True),
     "$100k\u2013$150k/yr (+equity, +bonus)"),
    # Currency=None falls back to no prefix
    (Compensation(100000, 150000, None, "annual"), "100k\u2013150k/yr"),
])
def test_format_compensation(c, expected):
    assert format_compensation(c) == expected


from job_scraper.scraper.greenhouse import _build_compensation as _gh_build


def test_greenhouse_pay_range():
    ranges = [{"min_cents": 16500000, "max_cents": 22500000,
               "currency_type": "USD"}]
    c = _gh_build(ranges)
    assert c == Compensation(min_amount=165000, max_amount=225000,
                             currency="USD", interval=None)


def test_greenhouse_eur():
    ranges = [{"min_cents": 9200000, "max_cents": 11500000,
               "currency_type": "EUR"}]
    c = _gh_build(ranges)
    assert c == Compensation(min_amount=92000, max_amount=115000,
                             currency="EUR", interval=None)


def test_greenhouse_empty_returns_none():
    assert _gh_build([]) is None
    assert _gh_build([{"min_cents": None, "max_cents": None}]) is None


def test_greenhouse_first_range_wins():
    # Multiple ranges (e.g. per-location) — take the first that has data.
    ranges = [
        {"min_cents": None, "max_cents": None},
        {"min_cents": 10000000, "max_cents": 12000000,
         "currency_type": "USD"},
    ]
    c = _gh_build(ranges)
    assert c == Compensation(min_amount=100000, max_amount=120000,
                             currency="USD", interval=None)


@pytest.mark.parametrize("currency_type", [None, ""])
def test_greenhouse_currency_falls_back_to_usd(currency_type):
    ranges = [{"min_cents": 10000000, "max_cents": 12000000,
               "currency_type": currency_type}]
    c = _gh_build(ranges)
    assert c is not None and c.currency == "USD"


from job_scraper.scraper.lever import _build_compensation as _lv_build


@pytest.mark.parametrize("salary, expected", [
    ({"min": 100000, "max": 115000, "currency": "USD",
      "interval": "per-year-salary"},
     Compensation(100000, 115000, "USD", "annual")),
    ({"min": 50, "max": 75, "currency": "USD",
      "interval": "per-hour-wage"},
     Compensation(50, 75, "USD", "hourly")),
    ({"min": 8000, "max": 10000, "currency": "EUR",
      "interval": "per-month-salary"},
     Compensation(8000, 10000, "EUR", "monthly")),
    # Unknown interval → None
    ({"min": 100000, "max": 115000, "currency": "USD",
      "interval": "per-fortnight-mystery"},
     Compensation(100000, 115000, "USD", None)),
    # Missing currency → None
    ({"min": 100000, "max": 115000, "interval": "per-year-salary"},
     Compensation(100000, 115000, None, "annual")),
    # Float hourly wage → truncated to int (Lever returns decimals here)
    ({"min": 37.02, "max": 59.13, "currency": "USD",
      "interval": "per-hour-wage"},
     Compensation(37, 59, "USD", "hourly")),
])
def test_lever_salary(salary, expected):
    assert _lv_build(salary) == expected


def test_lever_no_bounds_returns_none():
    assert _lv_build({"currency": "USD",
                      "interval": "per-year-salary"}) is None


from job_scraper.scraper.ashby import _build_compensation as _ab_build


@pytest.mark.parametrize("summary, expected", [
    ("$164,638 \u2013 $259,000",
     Compensation(164638, 259000, "USD", None)),
    ("$250K \u2013 $350K",
     Compensation(250000, 350000, "USD", None)),
    ("$160K \u2013 $190K",
     Compensation(160000, 190000, "USD", None)),
    ("$43 \u2013 $50",
     Compensation(43, 50, "USD", None)),
    # Equity / bonus markers
    ("$180K \u2013 $200K • Offers Equity",
     Compensation(180000, 200000, "USD", None, equity=True)),
    ("$110K \u2013 $160K • Offers Equity • Offers Bonus",
     Compensation(110000, 160000, "USD", None, equity=True, bonus=True)),
    # Hyphen separator (not en-dash)
    ("$100,000 - $120,000",
     Compensation(100000, 120000, "USD", None)),
])
def test_ashby_summary(summary, expected):
    assert _ab_build({"compensationTierSummary": summary}) == expected


def test_ashby_unparseable_returns_none():
    assert _ab_build({"compensationTierSummary": "Competitive"}) is None
    assert _ab_build({"compensationTierSummary": ""}) is None
    assert _ab_build({}) is None
