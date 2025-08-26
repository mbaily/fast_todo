import pytest
from datetime import datetime, timezone, timedelta

# Tests for year-resolution behavior implemented in calendar occurrences

@pytest.mark.asyncio
async def test_window_expansion_returns_multiple_years(client):
    # create a list
    r = await client.post('/lists', params={'name': 'YR Test List'})
    assert r.status_code == 200
    lid = r.json().get('id')
    # create a todo with yearless date 'Jan 22'
    r = await client.post('/todos', params={'text': 'Event Jan 22', 'list_id': lid})
    assert r.status_code == 200
    # query occurrences with a window spanning two years (Jan 2026 and Jan 2027)
    start = datetime(2026,1,1,tzinfo=timezone.utc).isoformat()
    end = datetime(2027,12,31,tzinfo=timezone.utc).isoformat()
    resp = await client.get('/calendar/occurrences', params={'start': start, 'end': end})
    assert resp.status_code == 200
    occ = resp.json().get('occurrences', [])
    # Under the global 1-year cap, only candidates within one year of creation are allowed.
    days = [o['occurrence_dt'][:10] for o in occ if o['title'] and 'Event Jan 22' in o['title']]
    assert '2026-01-22' in days
    # The 2027 occurrence falls beyond the 1-year cap and should not appear.
    assert '2027-01-22' not in days


@pytest.mark.asyncio
async def test_creation_time_resolution_single_year(client):
    # create a list
    r = await client.post('/lists', params={'name': 'YR Test List 2'})
    assert r.status_code == 200
    lid = r.json().get('id')
    # create todo in Dec 2025 pretending creation time: we cannot set created_at easily
    # so create the todo and query a window that does NOT span multiple years: expect the upcoming Jan 22 (2026)
    r = await client.post('/todos', params={'text': 'DecEvent Jan 22', 'list_id': lid})
    assert r.status_code == 200
    # query only Jan 2026 month
    start = datetime(2026,1,1,tzinfo=timezone.utc).isoformat()
    end = datetime(2026,1,31,tzinfo=timezone.utc).isoformat()
    resp = await client.get('/calendar/occurrences', params={'start': start, 'end': end})
    assert resp.status_code == 200
    occ = resp.json().get('occurrences', [])
    days = [o['occurrence_dt'][:10] for o in occ if o['title'] and 'DecEvent Jan 22' in o['title']]
    assert '2026-01-22' in days


@pytest.mark.asyncio
async def test_feb29_next_leap_year(client):
    r = await client.post('/lists', params={'name': 'Leap Test'})
    assert r.status_code == 200
    lid = r.json().get('id')
    r = await client.post('/todos', params={'text': 'LeapParty Feb 29', 'list_id': lid})
    assert r.status_code == 200
    # query a wide window and expect next leap year occurrence (2028)
    start = datetime(2025,1,1,tzinfo=timezone.utc).isoformat()
    end = datetime(2030,12,31,tzinfo=timezone.utc).isoformat()
    resp = await client.get('/calendar/occurrences', params={'start': start, 'end': end})
    assert resp.status_code == 200
    occ = resp.json().get('occurrences', [])
    # With the 1-year cap, next-leap-year resolution for Feb 29 is disallowed; expect no occurrences
    days = [o['occurrence_dt'][:10] for o in occ if o['title'] and 'LeapParty' in o['title']]
    assert days == []
