import pytest
from datetime import datetime, timezone
from app.utils import resolve_yearless_date


@pytest.mark.parametrize("month,day,created,expected_year", [
    (1, 2, datetime(2025, 12, 20, tzinfo=timezone.utc), 2026),  # late-year -> next year
    (1, 2, datetime(2025, 1, 2, tzinfo=timezone.utc), 2025),    # created on same day -> same year
    (12, 31, datetime(2025, 12, 30, tzinfo=timezone.utc), 2025), # created before end -> same year
    (12, 31, datetime(2025, 12, 31, tzinfo=timezone.utc), 2025), # created on date -> same year
    # With the global 1-year cap, Feb 29 is not allowed to resolve to a multi-year jump;
    # tests that previously expected the next leap year should now expect None.
    (2, 29, datetime(2025, 6, 1, tzinfo=timezone.utc), None),    # feb29 -> outside 1yr cap
    (2, 29, datetime(2028, 3, 1, tzinfo=timezone.utc), None),    # after Feb29 in leap year -> outside 1yr cap
    # same-day but created later than midnight: candidate at 00:00 should be considered past
    (1, 2, datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc), 2026),
    # created on Dec 31 and target Jan 1 -> next year
    (1, 1, datetime(2025, 12, 31, tzinfo=timezone.utc), 2026),
])
def test_resolve_yearless_various_unit(month, day, created, expected_year):
    res = resolve_yearless_date(month, day, created)
    if expected_year is None:
        assert res is None
    else:
        assert isinstance(res, datetime)
        assert res.year == expected_year


@pytest.mark.parametrize("month,day,window_start,window_end,expected_years", [
    # With the 1-year cap applied relative to created_at=2025-01-01, windows that fall
    # entirely beyond that cap will return no candidates.
    (1, 22, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2027, 12, 31, tzinfo=timezone.utc), []),
    (2, 29, datetime(2027, 1, 1, tzinfo=timezone.utc), datetime(2032, 12, 31, tzinfo=timezone.utc), []),
    # window that includes only a single leap year -> outside 1yr cap
    (2, 29, datetime(2027, 1, 1, tzinfo=timezone.utc), datetime(2028, 12, 31, tzinfo=timezone.utc), []),
])
def test_resolve_yearless_window_unit(month, day, window_start, window_end, expected_years):
    res = resolve_yearless_date(month, day, datetime(2025, 1, 1, tzinfo=timezone.utc), window_start, window_end)
    assert isinstance(res, list)
    years = [d.year for d in res]
    # expected_years may be empty under the global 1-year cap
    assert sorted(years) == sorted(expected_years)
