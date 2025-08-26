import pytest
from datetime import datetime, timezone, timedelta
from dateutil import rrule


@pytest.mark.asyncio
async def test_parse_text_to_rrule_occurrences(client):
    # Ask the API to parse a date + recurrence
    resp = await client.post('/parse_text_to_rrule', data={'text': '2025-08-25 every 2 weeks'})
    assert resp.status_code == 200
    j = resp.json()
    dt_iso = j.get('dtstart')
    rrule_body = j.get('rrule')
    assert dt_iso is not None
    assert rrule_body

    # parse dtstart
    dt = datetime.fromisoformat(dt_iso)
    # build rrule using rrulestr (needs the RRULE: prefix)
    r = rrule.rrulestr('RRULE:' + rrule_body, dtstart=dt)
    it = iter(r)
    first = next(it)
    assert first == dt
    second = next(it)
    assert second == (dt + timedelta(days=14))
