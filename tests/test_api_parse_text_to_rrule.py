import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_api_parse_text_to_rrule_with_recurrence(client):
    resp = await client.post('/parse_text_to_rrule', data={'text': '2025-08-25 every 2 weeks'})
    assert resp.status_code == 200
    j = resp.json()
    assert j.get('dtstart') == datetime(2025, 8, 25, tzinfo=timezone.utc).isoformat()
    assert 'FREQ=WEEKLY' in j.get('rrule', '')
    assert isinstance(j.get('rrule_params'), dict)


@pytest.mark.asyncio
async def test_api_parse_text_to_rrule_date_only(client):
    resp = await client.post('/parse_text_to_rrule', data={'text': '2025-08-25'})
    assert resp.status_code == 200
    j = resp.json()
    assert j.get('dtstart') == datetime(2025, 8, 25, tzinfo=timezone.utc).isoformat()
    assert j.get('rrule') == ''
    assert j.get('rrule_params') is None


@pytest.mark.asyncio
async def test_api_parse_text_to_rrule_no_date(client):
    resp = await client.post('/parse_text_to_rrule', data={'text': 'every week'})
    assert resp.status_code == 200
    j = resp.json()
    assert j.get('dtstart') is None
    assert j.get('rrule') == ''
    assert j.get('rrule_params') is None
