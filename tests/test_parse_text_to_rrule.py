from app.utils import parse_text_to_rrule
from datetime import datetime, timezone


def test_parse_text_to_rrule_with_recurrence():
    r, dt = parse_text_to_rrule('2025-08-25 every 2 weeks')
    assert dt == datetime(2025, 8, 25, tzinfo=timezone.utc)
    assert r is not None
    # rrule is iterable; get first two occurrences
    it = iter(r)
    first = next(it)
    assert first == dt


def test_parse_text_to_rrule_date_only():
    r, dt = parse_text_to_rrule('2025-08-25')
    assert dt == datetime(2025, 8, 25, tzinfo=timezone.utc)
    assert r is None


def test_parse_text_to_rrule_no_date():
    r, dt = parse_text_to_rrule('every week')
    assert r is None
    assert dt is None
