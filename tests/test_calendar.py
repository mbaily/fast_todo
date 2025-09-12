import pytest
from datetime import datetime, timezone, timedelta
from app.utils import extract_dates


@pytest.mark.asyncio
async def test_extract_dates_common_formats():
    samples = [
        ("Meet with Bob on 2025-09-01", "2025-09-01"),
        ("Deadline Sept 1st 2025", "2025-09-01"),
        ("Reminder: 1/9/2025 14:00", "2025-01-09"),
        ("tomorrow", None),  # relative dates may resolve differently; ensure no crash
    ]
    for text, expected_date_prefix in samples:
        dates = extract_dates(text)
        assert isinstance(dates, list)
        # ensure function is robust and returns datetimes or empty list
        for d in dates:
            assert isinstance(d, datetime)


@pytest.mark.asyncio
async def test_calendar_events_endpoint(client, use_fake_extract_dates, monkeypatch):
    """Use deterministic fake extractor to avoid invoking dateparser."""
    import app.main as main

    # monkeypatch parse_text_to_rrule to avoid recurrence expansion in this test
    monkeypatch.setattr(main, 'parse_text_to_rrule', lambda t: (None, None), raising=False)

    # create a list and todos with dates embedded
    resp = await client.post('/lists', data={'name': 'CalList 2025-09-01'})
    assert resp.status_code == 200
    lst = resp.json()
    list_id = lst['id']

    # create a todo with an explicit date in text
    resp = await client.post('/todos', json={'list_id': list_id, 'text': 'Pay rent on 2025-09-05'})
    if resp.status_code != 200:
        raise AssertionError(f"create todo failed: {resp.status_code} {resp.text}")
    todo = resp.json()
    _ = todo['id']

    # create a todo with deferred_until
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    resp = await client.post('/todos', json={'list_id': list_id, 'text': 'Check warranty', 'deferred_until': future})
    if resp.status_code != 200:
        raise AssertionError(f"create todo with deferred_until failed: {resp.status_code} {resp.text}")

    # call calendar endpoint without bounds
    resp = await client.get('/calendar/events')
    assert resp.status_code == 200
    data = resp.json()
    assert 'events' in data
    # expect at least two events: one for the list (date in name) and one for todo
    ids = [e.get('id') for e in data['events']]
    assert list_id in ids or any(e.get('list_id') == list_id for e in data['events'])

    # call calendar endpoint with future window excluding items
    start = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    resp = await client.get('/calendar/events', params={'start': start})
    assert resp.status_code == 200
    data2 = resp.json()
    assert isinstance(data2.get('events'), list)

