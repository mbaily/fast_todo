import pytest
from datetime import date, timezone
from app.utils import extract_dates


def to_ymd(dt):
    # normalize to UTC date
    return dt.astimezone(timezone.utc).date()


def test_extract_dates_iso_date_only():
    txt = "Event on 2025-09-01"
    res = extract_dates(txt)
    assert isinstance(res, list)
    assert len(res) == 1
    assert to_ymd(res[0]) == date(2025, 9, 1)


def test_extract_dates_iso_with_time():
    txt = "Starts 2025-09-01 14:30"
    res = extract_dates(txt)
    assert len(res) == 1
    # date component must match
    assert to_ymd(res[0]) == date(2025, 9, 1)


def test_extract_dates_multiple_dates():
    txt = "Phase 1: 2025-09-01. Phase 2: 2025-09-05 09:00"
    res = extract_dates(txt)
    assert len(res) >= 2
    dates = {to_ymd(d) for d in res}
    assert date(2025, 9, 1) in dates
    assert date(2025, 9, 5) in dates


def test_extract_dates_with_timezone_offset():
    # include explicit timezone; result should be timezone-aware and normalized to UTC
    txt = "Call 2025-09-01 14:00 PST"
    res = extract_dates(txt)
    assert len(res) >= 1
    d = res[0]
    assert d.tzinfo is not None
    assert to_ymd(d) == date(2025, 9, 1)


def test_extract_dates_no_date_returns_empty():
    assert extract_dates('no dates here') == []
