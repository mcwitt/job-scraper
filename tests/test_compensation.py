"""Tests for Compensation dataclass and format_compensation."""

import dataclasses

import dacite
import pytest

from job_scraper.models import Compensation, Job, to_dict


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


def test_ashby_unparseable_falls_back_to_raw_string():
    assert _ab_build({"compensationTierSummary": "Competitive"}) == "Competitive"
    assert _ab_build(
        {"compensationTierSummary": "CA$121.5K \u2013 CA$166.6K"}
    ) == "CA$121.5K \u2013 CA$166.6K"


def test_ashby_empty_summary_returns_none():
    assert _ab_build({"compensationTierSummary": ""}) is None
    assert _ab_build({}) is None


@pytest.mark.parametrize("summary, expected", [
    # Decimals with K
    ("$234.4K \u2013 $385K • Offers Equity",
     Compensation(234400, 385000, "USD", None, equity=True)),
    ("$162.4K \u2013 $225K",
     Compensation(162400, 225000, "USD", None)),
    # Single value with K
    ("$380K • Offers Equity",
     Compensation(380000, None, "USD", None, equity=True)),
    # Hourly with decimals (no K)
    ("$60.58 \u2013 $67.31 per hour • Offers Equity",
     Compensation(60, 67, "USD", "hourly", equity=True)),
    # Monthly single value
    ("$11.7K per month",
     Compensation(11700, None, "USD", "monthly")),
    # Annual marker
    ("$120K \u2013 $150K per year",
     Compensation(120000, 150000, "USD", "annual")),
])
def test_ashby_extended_formats(summary, expected):
    assert _ab_build({"compensationTierSummary": summary}) == expected


def test_ashby_summary_components_preferred_over_regex():
    """summaryComponents handle non-USD currencies the regex can't."""
    comp = {
        "compensationTierSummary": "PLN 16.4K \u2013 PLN 35.1K per month",
        "summaryComponents": [{
            "compensationType": "Salary",
            "interval": "1 MONTH",
            "currencyCode": "PLN",
            "minValue": 16400,
            "maxValue": 35100,
        }],
    }
    assert _ab_build(comp) == Compensation(
        16400, 35100, "PLN", "monthly",
    )


def test_ashby_summary_components_fold_bonus_and_equity():
    comp = {
        "compensationTierSummary": "$110K \u2013 $130K",
        "summaryComponents": [
            {
                "compensationType": "Salary",
                "interval": "1 YEAR",
                "currencyCode": "USD",
                "minValue": 110000,
                "maxValue": 130000,
            },
            {
                "compensationType": "Bonus",
                "minValue": None,
                "maxValue": None,
                "interval": "1 YEAR",
            },
            {
                "compensationType": "EquityPercentage",
                "minValue": None,
                "maxValue": None,
                "interval": "NONE",
            },
        ],
    }
    assert _ab_build(comp) == Compensation(
        110000, 130000, "USD", "annual",
        equity=True, bonus=True,
    )


def test_ashby_components_without_salary_falls_back_to_summary():
    comp = {
        "compensationTierSummary": "$100K \u2013 $120K",
        "summaryComponents": [{
            "compensationType": "EquityPercentage",
            "minValue": None,
            "maxValue": None,
            "interval": "NONE",
        }],
    }
    assert _ab_build(comp) == Compensation(
        100000, 120000, "USD", None,
    )


from job_scraper.scraper.rippling import _build_compensation as _rp_build


@pytest.mark.parametrize("pay_ranges, expected", [
    (
        [{
            "currency": "USD",
            "frequency": "YEAR",
            "rangeStart": 220000.0,
            "rangeEnd": 265000.0,
        }],
        Compensation(220000, 265000, "USD", "annual"),
    ),
    (
        [{
            "currency": "USD",
            "frequency": "HOUR",
            "rangeStart": 45.0,
            "rangeEnd": 60.0,
        }],
        Compensation(45, 60, "USD", "hourly"),
    ),
    (
        [{
            "currency": "EUR",
            "frequency": "MONTH",
            "rangeStart": 8000.0,
            "rangeEnd": 10000.0,
        }],
        Compensation(8000, 10000, "EUR", "monthly"),
    ),
    # Unknown frequency → interval=None
    (
        [{
            "currency": "USD",
            "frequency": "FORTNIGHT",
            "rangeStart": 1000.0,
            "rangeEnd": 2000.0,
        }],
        Compensation(1000, 2000, "USD", None),
    ),
])
def test_rippling_pay_range(pay_ranges, expected):
    assert _rp_build(pay_ranges) == expected


def test_rippling_empty_returns_none():
    assert _rp_build([]) is None
    assert _rp_build([{"rangeStart": None, "rangeEnd": None}]) is None


def test_rippling_first_populated_wins():
    pay_ranges = [
        {"rangeStart": None, "rangeEnd": None},
        {
            "currency": "USD",
            "frequency": "YEAR",
            "rangeStart": 150000.0,
            "rangeEnd": 200000.0,
        },
    ]
    assert _rp_build(pay_ranges) == Compensation(
        150000, 200000, "USD", "annual",
    )


from job_scraper.scraper.smartrecruiters import (
    _build_compensation as _sr_build,
)


@pytest.mark.parametrize("comp, expected", [
    (
        {"min": 15000, "max": 15000, "currency": "USD",
         "period": "MONTHLY"},
        Compensation(15000, 15000, "USD", "monthly"),
    ),
    (
        {"min": 120000, "max": 150000, "currency": "USD",
         "period": "ANNUAL"},
        Compensation(120000, 150000, "USD", "annual"),
    ),
    # Unknown period → None
    (
        {"min": 100, "max": 200, "currency": "USD", "period": "DAILY"},
        Compensation(100, 200, "USD", None),
    ),
])
def test_smartrecruiters_compensation(comp, expected):
    assert _sr_build(comp) == expected


@pytest.mark.parametrize("comp", [
    None,
    {},
    # min=0 is SR's placeholder for unset; treat as no data.
    {"min": 0, "currency": "USD", "period": "MONTHLY"},
    {"min": 0, "max": 0, "currency": "USD", "period": "MONTHLY"},
])
def test_smartrecruiters_no_data_returns_none(comp):
    assert _sr_build(comp) is None


from job_scraper.comp_text import extract_from_text


@pytest.mark.parametrize("text, expected", [
    # Google's standard US pay-transparency line
    (
        "The US base salary range for this full-time position is "
        "$174,000-$252,000 + bonus + equity + benefits.",
        Compensation(174000, 252000, "USD", "annual"),
    ),
    # Workday (Cadence) state-specific phrasing
    (
        "The annual salary range for California is $101,500 to $188,500. "
        "You may also be eligible to receive incentive compensation.",
        Compensation(101500, 188500, "USD", "annual"),
    ),
    # Workday (NVIDIA-ish) position phrasing
    (
        "The base salary range for this position is $148,500 - $313,700.",
        Compensation(148500, 313700, "USD", "annual"),
    ),
    # Phenom/Snowflake role phrasing
    (
        "The estimated base salary range for this role is "
        "$160,000 - $230,000.",
        Compensation(160000, 230000, "USD", "annual"),
    ),
    # Netflix market-range phrasing
    (
        "The range for this role is $255,000.00 - $400,000.00. "
        "Netflix provides comprehensive benefits.",
        Compensation(255000, 400000, "USD", "annual"),
    ),
    # iCIMS (Rivian) "for this role" phrasing
    (
        "The salary range for this role is $71,300 to $89,100.",
        Compensation(71300, 89100, "USD", "annual"),
    ),
    # Microsoft Research with USD prefix
    (
        "The base pay range for this role across the U.S. is "
        "USD $119,800 - $234,700 per year.",
        Compensation(119800, 234700, "USD", "annual"),
    ),
    # K-notation
    (
        "Compensation: $150K - $250K",
        Compensation(150000, 250000, "USD", "annual"),
    ),
    # Hourly explicit
    (
        "The hourly pay range for this role is $28 to $35 per hour.",
        Compensation(28, 35, "USD", "hourly"),
    ),
    # Magnitude-based hourly inference (no marker)
    (
        "The pay range for this role is $43 - $50.",
        Compensation(43, 50, "USD", "hourly"),
    ),
    # "Salary Range/Hourly Rate range" boilerplate (Rivian) — "hourly"
    # keyword in window with annual amounts → annual.
    (
        "Salary Range/Hourly Rate range for California Based "
        "Applicants: $171,100-$213,900",
        Compensation(171100, 213900, "USD", "annual"),
    ),
    # "an hour" trumps an earlier "annual" in the same sentence
    (
        "The annual salary range for California is $39.71 to $73.75 "
        "an hour.",
        Compensation(39, 73, "USD", "hourly"),
    ),
])
def test_extract_from_text(text, expected):
    assert extract_from_text(text) == expected


@pytest.mark.parametrize("text", [
    "",
    "We offer a competitive salary.",
    "Our revenue grew from $10M to $50M last year.",
    # Phrase present but no amounts
    "We have a transparent salary range policy.",
    # Backwards range (hi < lo) rejected
    "salary range is $200,000 to $100,000",
])
def test_extract_from_text_negatives(text):
    assert extract_from_text(text) is None


def test_format_compensation_passes_through_raw_string():
    assert format_compensation("Competitive") == "Competitive"
    assert format_compensation("CA$121K \u2013 CA$166K") == "CA$121K \u2013 CA$166K"


def test_job_round_trip_with_raw_string_compensation():
    job = Job(
        hash="h",
        title="t",
        company="c",
        url="u",
        description="d",
        source="ashby:x",
        compensation="Competitive",
    )
    d = to_dict(job)
    restored = dacite.from_dict(Job, d)
    assert restored.compensation == "Competitive"


def test_job_round_trip_with_structured_compensation():
    comp = Compensation(100000, 150000, "USD", "annual", equity=True)
    job = Job(
        hash="h",
        title="t",
        company="c",
        url="u",
        description="d",
        source="lever:x",
        compensation=comp,
    )
    d = to_dict(job)
    restored = dacite.from_dict(Job, d)
    assert restored.compensation == comp
