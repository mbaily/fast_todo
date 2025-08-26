from app.utils import parse_text_to_rrule_string
from datetime import datetime, timezone


def test_parse_text_to_rrule_string_with_recurrence():
    text = '2025-08-25 every 2 weeks'
    dt, r = parse_text_to_rrule_string(text)
    assert isinstance(dt, datetime)
    assert dt == datetime(2025, 8, 25, tzinfo=timezone.utc)
    assert 'FREQ=WEEKLY' in r
    assert 'INTERVAL=2' in r


def test_parse_text_to_rrule_string_date_only():
    text = '2025-08-25'
    dt, r = parse_text_to_rrule_string(text)
    assert isinstance(dt, datetime)
    assert dt == datetime(2025, 8, 25, tzinfo=timezone.utc)
    assert r == ''


def test_parse_text_to_rrule_string_no_date():
    dt, r = parse_text_to_rrule_string('every week')
    assert dt is None
    assert r == ''
