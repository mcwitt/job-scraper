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
