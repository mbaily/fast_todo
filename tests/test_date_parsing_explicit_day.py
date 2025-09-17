import pytest
from datetime import datetime, timezone

from app.utils import extract_dates_meta, parse_text_to_rrule_string


def test_explicit_day_month_year_preferred():
    text = "Next payment $1,023.30 17 September 2025"
    metas = extract_dates_meta(text)
    assert metas, "No dates extracted from explicit day-month-year"
    # The best match should reflect the explicit 17 September 2025
    best = metas[0]
    assert best.get('day_explicit') is True
    assert best.get('year_explicit') is True
    dt = best['dt']
    assert dt.year == 2025 and dt.month == 9 and dt.day == 17


def test_parse_text_to_rrule_string_returns_explicit_dt():
    text = "Next payment $1,023.30 17 September 2025"
    dt, rrule = parse_text_to_rrule_string(text)
    assert rrule == ''  # no recurrence phrase
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None
    assert dt.year == 2025 and dt.month == 9 and dt.day == 17
