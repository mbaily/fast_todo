import pytest
from datetime import datetime, timezone, timedelta


@pytest.mark.asyncio
async def test_calendar_occurrences_expansion(client, monkeypatch, use_fake_extract_dates):
    """Use deterministic fake extractor and stub recurrence parsing so the
    test doesn't invoke dateparser internals.
    """
    import app.main as main

    # stub parse_text_to_rrule so endpoint won't attempt to build/expand recurrences
    def _stub_parse(text):
        return None, None

    monkeypatch.setattr(main, 'parse_text_to_rrule', _stub_parse, raising=False)

    resp = await client.post('/lists', data={'name': 'COList'})
    assert resp.status_code == 200
    lst = resp.json()
    list_id = lst['id']

    # create a todo containing a recurring phrase (anchor date 2025-08-25)
    resp = await client.post('/todos', json={'list_id': list_id, 'text': 'Pay subscription on 2025-08-25 every 2 weeks'})
    assert resp.status_code == 200
    todo = resp.json()

    # request occurrences for next 30 days
    start = datetime(2025, 8, 24, tzinfo=timezone.utc).isoformat()
    end = (datetime(2025, 8, 24, tzinfo=timezone.utc) + timedelta(days=30)).isoformat()
    resp = await client.get('/calendar/occurrences', params={'start': start, 'end': end})
    assert resp.status_code == 200
    j = resp.json()
    occs = j.get('occurrences')
    assert isinstance(occs, list)
    # Expect at least one occurrence returned for the created todo
    assert len(occs) >= 1
    # basic shape checks
    assert all('occurrence_dt' in o for o in occs)


@pytest.mark.asyncio
async def test_calendar_occurrences_truncation(client, monkeypatch, use_fake_extract_dates):
    """Use deterministic fake extractor and stub parse_text_to_rrule to
    return a daily rrule for texts that include 'every day'. This avoids
    invoking dateparser while still exercising truncation.
    """
    import re
    from dateutil import rrule as _rrule

    def _stub_parse(text):
        # If text contains an ISO date and 'every day', return a daily rrule
        if not text:
            return None, None
        m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        if m and 'every day' in text:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
            r = _rrule.rrule(_rrule.DAILY, dtstart=dt)
            return r, dt
        return None, None

    import app.main as main
    monkeypatch.setattr(main, 'parse_text_to_rrule', _stub_parse, raising=False)

    resp = await client.post('/lists', data={'name': 'BigList'})
    lst = resp.json()
    list_id = lst['id']

    # create a todo that recurs daily starting 2025-01-01 (this will generate many occurrences)
    resp = await client.post('/todos', json={'list_id': list_id, 'text': 'Daily meeting 2025-01-01 every day'})
    assert resp.status_code == 200

    # request a large window but set max_total small to force truncation
    start = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()
    end = (datetime(2026, 1, 1, tzinfo=timezone.utc)).isoformat()
    resp = await client.get('/calendar/occurrences', params={'start': start, 'end': end, 'max_total': 5})
    assert resp.status_code == 200
    j = resp.json()
    assert j.get('truncated') is True
    assert len(j.get('occurrences', [])) <= 5
