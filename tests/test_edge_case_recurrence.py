from datetime import datetime, timezone
from dateutil import rrule as dr
from app.utils import (
    parse_recurrence_phrase,
    recurrence_dict_to_rrule_params,
    build_rrule_from_recurrence,
    parse_text_to_rrule_string,
)


def test_every_weekday_parse_and_rrule_string():
    rec = parse_recurrence_phrase('every weekday')
    assert rec == {'freq': 'WEEKLY', 'byweekday': ['MO', 'TU', 'WE', 'TH', 'FR']}
    s = recurrence_dict_to_rrule_params(rec)
    assert s['freq'] == dr.WEEKLY
    assert isinstance(s['byweekday'], tuple)
    # check RRULE string
    from app.utils import recurrence_dict_to_rrule_string
    rstr = recurrence_dict_to_rrule_string(rec)
    assert 'BYDAY=MO' in rstr and 'TU' in rstr and 'FR' in rstr


def test_every_monday_params_and_string():
    rec = parse_recurrence_phrase('every monday')
    assert rec['freq'] == 'WEEKLY'
    assert rec['byweekday'] == ['MO']
    params = recurrence_dict_to_rrule_params(rec)
    assert params['freq'] == dr.WEEKLY
    assert params['byweekday'][0] == dr.MO


def test_bymonthday_rrule_occurrences():
    rec = {'freq': 'MONTHLY', 'bymonthday': 15}
    dt = datetime(2025, 1, 15, tzinfo=timezone.utc)
    r = build_rrule_from_recurrence(rec, dt)
    it = iter(r)
    first = next(it)
    assert first == dt
    second = next(it)
    assert second == datetime(2025, 2, 15, tzinfo=timezone.utc)


def test_every_other_week_parse():
    rec = parse_recurrence_phrase('every other week')
    assert rec == {'freq': 'WEEKLY', 'interval': 2}


def test_last_friday_unsupported_returns_no_rrule():
    # our heuristic parser doesn't support "last friday" phrasing yet
    dt, r = parse_text_to_rrule_string('2025-08-29 the last friday of every month')
    assert isinstance(dt, datetime)
    # parser now supports last-weekday-of-month -> expect BYSETPOS=-1 and BYDAY=FR
    assert 'FREQ=MONTHLY' in r
    assert 'BYSETPOS=-1' in r
    assert 'BYDAY=FR' in r


def test_multiple_weekdays_params():
    rec = {'freq': 'WEEKLY', 'byweekday': ['MO', 'WE', 'FR']}
    params = recurrence_dict_to_rrule_params(rec)
    assert isinstance(params['byweekday'], tuple)
    assert params['byweekday'][0] == dr.MO
    assert params['byweekday'][1] == dr.WE
    assert params['byweekday'][2] == dr.FR
