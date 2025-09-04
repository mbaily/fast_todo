import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_calendar_occurrences_inline_recurring_expansion(client):
    # create a list
    resp = await client.post('/lists', data={'name': 'RecList'})
    assert resp.status_code == 200
    lst = resp.json()
    list_id = lst['id']

    # add a todo with inline recurrence (anchor date 2025-08-11 derived from 5/8)
    resp = await client.post('/todos', json={'list_id': list_id, 'text': 'Water plant 5/8 every 2 weeks'})
    assert resp.status_code == 200

    # request occurrences covering Sep and Oct 2025
    start = datetime(2025, 9, 1, tzinfo=timezone.utc).isoformat()
    end = datetime(2025, 10, 31, tzinfo=timezone.utc).isoformat()
    resp = await client.get('/calendar/occurrences', params={'start': start, 'end': end})
    assert resp.status_code == 200
    j = resp.json()
    occs = j.get('occurrences', [])
    # Expect at least one occurrence in September and one in October for the 2-week recurrence
    dates = [o.get('occurrence_dt') for o in occs]
    assert any(d.startswith('2025-09') for d in dates), f'no sept occurrence in {dates}'
    assert any(d.startswith('2025-10') for d in dates), f'no oct occurrence in {dates}'
