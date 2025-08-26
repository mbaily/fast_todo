import pytest
from datetime import datetime, timezone
from app.utils import resolve_yearless_date


@pytest.mark.parametrize("month,day,created,expected_year", [
    (1, 2, datetime(2025, 12, 20, tzinfo=timezone.utc), 2026),  # late-year -> next year
    (1, 2, datetime(2025, 1, 2, tzinfo=timezone.utc), 2025),    # created on same day -> same year
    (12, 31, datetime(2025, 12, 30, tzinfo=timezone.utc), 2025), # created before end -> same year
    (12, 31, datetime(2025, 12, 31, tzinfo=timezone.utc), 2025), # created on date -> same year
    (2, 29, datetime(2025, 6, 1, tzinfo=timezone.utc), 2028),    # feb29 -> next leap year
    (2, 29, datetime(2028, 3, 1, tzinfo=timezone.utc), 2032),    # after Feb29 in leap year -> next leap
    # same-day but created later than midnight: candidate at 00:00 should be considered past
    (1, 2, datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc), 2026),
    # created on Dec 31 and target Jan 1 -> next year
    (1, 1, datetime(2025, 12, 31, tzinfo=timezone.utc), 2026),
])
def test_resolve_yearless_various_unit(month, day, created, expected_year):
    res = resolve_yearless_date(month, day, created)
    assert isinstance(res, datetime)
    assert res.year == expected_year


@pytest.mark.parametrize("month,day,window_start,window_end,expected_years", [
    (1, 22, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2027, 12, 31, tzinfo=timezone.utc), [2026, 2027]),
    (2, 29, datetime(2027, 1, 1, tzinfo=timezone.utc), datetime(2032, 12, 31, tzinfo=timezone.utc), [2028, 2032]),
    # window that includes only a single leap year
    (2, 29, datetime(2027, 1, 1, tzinfo=timezone.utc), datetime(2028, 12, 31, tzinfo=timezone.utc), [2028]),
])
def test_resolve_yearless_window_unit(month, day, window_start, window_end, expected_years):
    res = resolve_yearless_date(month, day, datetime(2025, 1, 1, tzinfo=timezone.utc), window_start, window_end)
    assert isinstance(res, list)
    years = [d.year for d in res]
    for y in expected_years:
        assert y in years
