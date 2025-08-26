from datetime import datetime, timezone
from app.utils import recurrence_dict_to_rrule_params, build_rrule_from_recurrence


def test_recurrence_dict_to_rrule_params_weekly():
    rec = {'freq': 'WEEKLY', 'interval': 2, 'byweekday': ['MO']}
    params = recurrence_dict_to_rrule_params(rec)
    assert 'freq' in params
    assert params['interval'] == 2
    assert isinstance(params['byweekday'], tuple)


def test_build_rrule_and_next_occurrence():
    rec = {'freq': 'WEEKLY', 'interval': 2, 'byweekday': ['MO']}
    dt = datetime(2025, 8, 25, 9, 0, tzinfo=timezone.utc)
    r = build_rrule_from_recurrence(rec, dt)
    # rrule is iterable but not an iterator; create an iterator explicitly
    it = iter(r)
    first = next(it)
    assert first == dt
    # second occurrence should be two weeks later
    second = next(it)
    assert second == datetime(2025, 9, 8, 9, 0, tzinfo=timezone.utc)
