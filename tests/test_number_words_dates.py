from datetime import datetime, timezone
from app.utils import extract_dates, parse_date_and_recurrence


def test_number_word_not_parsed():
    # single token number-words should not be interpreted as dates
    assert extract_dates('eight') == []
    dt, rec = parse_date_and_recurrence('eight')
    assert dt is None and rec is None


def test_numeric_short_token_not_parsed():
    # short numeric tokens like '8' should not be treated as a month/date
    assert extract_dates('8') == []
    dt, rec = parse_date_and_recurrence('8')
    assert dt is None and rec is None


def test_month_name_still_parsed():
    # genuine month names should still be recognized
    dates = extract_dates('August')
    assert len(dates) >= 1
    # returned datetime should be timezone-aware UTC
    assert isinstance(dates[0], datetime)
    assert dates[0].tzinfo == timezone.utc
