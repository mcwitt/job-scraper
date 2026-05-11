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
