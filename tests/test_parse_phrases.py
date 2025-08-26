import json
from pathlib import Path

import pytest

from app.utils import parse_text_to_rrule_string


TEST_FILE = Path(__file__).with_name('recurrence_phrases.json')


EXPECTED = {
    "Water pot plant every 2nd Sunday of every month": "FREQ=WEEKLY;INTERVAL=2;BYDAY=SU",
    "Pay rent on 2025-09-01 every month": "FREQ=MONTHLY",
    "Team sync 2025-08-01 every week": "FREQ=WEEKLY",
    "Gym every other day": "FREQ=DAILY;INTERVAL=2",
    "Backup on the last friday of every month": "FREQ=MONTHLY;BYSETPOS=-1;BYDAY=FR",
    "Standup every weekday": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "Mailing list every 2 weeks": "FREQ=WEEKLY;INTERVAL=2",
    "Water plant 5/8 every 2 weeks": "FREQ=WEEKLY;INTERVAL=2",
    "Pay subscription on 2025-08-25 every 2 weeks": "FREQ=WEEKLY;INTERVAL=2",
    "Billing every 3 months": "FREQ=MONTHLY;INTERVAL=3",
    "Anniversary on Sep 15 every year": "FREQ=YEARLY",
    "Payday every month on the 1st": "FREQ=MONTHLY",
    "Class every Monday": "FREQ=WEEKLY;BYDAY=MO",
    "Review every 2nd Tuesday": "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU",
    "Biweekly meeting every other week": "FREQ=WEEKLY;INTERVAL=2",
    "Quarterly review every 3 months": "FREQ=MONTHLY;INTERVAL=3",
    "Monthly report the 2nd sunday of every month": "FREQ=MONTHLY;BYSETPOS=2;BYDAY=SU",
    "Pay taxes every year": "FREQ=YEARLY",
    "Exercise every day": "FREQ=DAILY",
    "Checkups every 6 months": "FREQ=MONTHLY;INTERVAL=6",
    "Clean filter every 90 days": "FREQ=DAILY;INTERVAL=90",
    "Happy hour every thursday": "FREQ=WEEKLY;BYDAY=TH",
    "Rotate tires every 12 months": "FREQ=MONTHLY;INTERVAL=12",
    "Rotate backups every other month": "FREQ=MONTHLY;INTERVAL=2",
    "Every Monday morning meeting": "FREQ=WEEKLY;BYDAY=MO",
    "Pay rent on the last day of every month": "FREQ=MONTHLY;BYMONTHDAY=-1",
    "Water pot plant every 2nd Sunday of every month at 9am": "FREQ=WEEKLY;INTERVAL=2;BYDAY=SU",
}


@pytest.mark.parametrize('item', json.load(open(TEST_FILE)))
def test_parse_phrases_produce_expected_rrule(item):
    text = item.get('text') if isinstance(item, dict) else item
    dt, r = parse_text_to_rrule_string(text)
    assert dt is not None, f"dtstart missing for: {text}"
    assert isinstance(r, str), f"rrule not string for: {text}"
    expected = EXPECTED.get(text)
    assert expected is not None, f"no expected rrule provided for: {text}"
    # Normalize spacing & case before comparison
    assert r.strip().upper() == expected.strip().upper(), f"rrule mismatch for '{text}': got '{r}' expected '{expected}'"
