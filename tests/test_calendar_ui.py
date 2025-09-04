import pytest


@pytest.mark.asyncio
async def test_calendar_page_shows_events(client, use_fake_extract_dates):
    # create a list and a todo with an ISO date in the text (fake extractor will pick it up)
    resp = await client.post('/lists', data={'name': 'CalList 2025-09'})
    assert resp.status_code == 200
    lst = resp.json()
    list_id = lst['id']

    resp = await client.post('/todos', json={'list_id': list_id, 'text': 'Pay rent on 2025-09-05'})
    assert resp.status_code == 200

    # fetch calendar for September 2025 and select day 5
    resp = await client.get('/html_no_js/calendar', params={'year': 2025, 'month': 9, 'selected_day': 5})
    assert resp.status_code == 200
    body = resp.text
    assert 'Calendar - 2025-09' in body
    # event title/text should appear
    assert 'Pay rent' in body
