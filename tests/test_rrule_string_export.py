from app.utils import recurrence_dict_to_rrule_string


def test_rrule_string_basic_weekly():
    rec = {'freq': 'WEEKLY', 'interval': 2, 'byweekday': ['MO']}
    s = recurrence_dict_to_rrule_string(rec)
    # order may vary, check components
    assert 'FREQ=WEEKLY' in s
    assert 'INTERVAL=2' in s
    assert 'BYDAY=MO' in s


def test_rrule_string_monthly_bysetpos():
    rec = {'freq': 'MONTHLY', 'byweekday': ['SU'], 'bysetpos': 2}
    s = recurrence_dict_to_rrule_string(rec)
    assert 'FREQ=MONTHLY' in s
    assert 'BYDAY=SU' in s
    assert 'BYSETPOS=2' in s


def test_rrule_string_bymonthday():
    rec = {'freq': 'MONTHLY', 'bymonthday': 15}
    s = recurrence_dict_to_rrule_string(rec)
    assert 'FREQ=MONTHLY' in s
    assert 'BYMONTHDAY=15' in s
