import pytest
from app.utils import parse_recurrence_phrase, parse_date_and_recurrence
from datetime import datetime, timezone


def test_parse_recurrence_every_n_weeks():
    r = parse_recurrence_phrase('every 2 weeks')
    assert r == {'freq': 'WEEKLY', 'interval': 2}


def test_parse_recurrence_every_other_month():
    r = parse_recurrence_phrase('every other month')
    assert r == {'freq': 'MONTHLY', 'interval': 2}


def test_parse_recurrence_recurring_monthly():
    r = parse_recurrence_phrase('recurring monthly')
    assert r == {'freq': 'MONTHLY'}


def test_parse_recurrence_the_2nd_sunday_of_every_month():
    r = parse_recurrence_phrase('the 2nd sunday of every month')
    assert r == {'freq': 'MONTHLY', 'byweekday': ['SU'], 'bysetpos': 2}


def test_parse_date_and_recurrence_with_phrase():
    dt, rec = parse_date_and_recurrence('2025-08-25 every 2 weeks')
    assert isinstance(dt, datetime)
    assert rec == {'freq': 'WEEKLY', 'interval': 2}
