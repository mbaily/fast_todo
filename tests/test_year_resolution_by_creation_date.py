from datetime import datetime, timezone

from app.utils import resolve_yearless_date


def test_resolve_yearless_uses_creation_date_next_year():
    # created late in year, Jan 2 should resolve to next calendar year
    created = datetime(2025, 12, 20, tzinfo=timezone.utc)
    res = resolve_yearless_date(1, 2, created)
    assert isinstance(res, datetime)
    assert res.year == 2026
    assert res.month == 1 and res.day == 2


def test_resolve_yearless_same_year_when_future_in_same_year():
    # created early in year, Jan 2 should resolve to same year
    created = datetime(2025, 1, 1, tzinfo=timezone.utc)
    res = resolve_yearless_date(1, 2, created)
    assert isinstance(res, datetime)
    assert res.year == 2025


def test_resolve_feb29_picks_next_leap_year():
    # created in 2025, next Feb 29 is 2028
    created = datetime(2025, 6, 1, tzinfo=timezone.utc)
    res = resolve_yearless_date(2, 29, created)
    # With a strict 1-year cap and no leap-year exception, there is no
    # valid Feb 29 inside the allowed window (2025-06-01 .. 2026-06-01).
    assert res is None


def test_window_returns_multiple_candidates():
    # window spanning 2026-2027 should return both Jan 22 candidates
    window_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    window_end = datetime(2027, 12, 31, tzinfo=timezone.utc)
    created = datetime(2025, 8, 1, tzinfo=timezone.utc)
    res = resolve_yearless_date(1, 22, created, window_start, window_end)
    assert isinstance(res, list)
    years = [d.year for d in res]
    # With a global 1-year cap (created 2025-08-01 -> cap 2026-08-01), only
    # the 2026 candidate falls inside the allowed range; 2027 should be
    # excluded.
    assert 2026 in years and 2027 not in years
