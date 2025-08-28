import pytest
from datetime import datetime, timezone
from sqlmodel import select

from app.db import async_session
from app.models import Todo


@pytest.mark.asyncio
async def test_plain_dates_resolve_to_next_year_only(client):
    # create a list
    r = await client.post('/lists', params={'name': 'Plain Date List'})
    assert r.status_code == 200
    lid = r.json().get('id')

    # create two todos with plain dates (US-style M/D in text)
    r1 = await client.post('/todos', params={'text': 'New Year 1/1', 'list_id': lid})
    assert r1.status_code == 200
    tid1 = r1.json().get('id')

    r2 = await client.post('/todos', params={'text': 'Five Mar 5/3', 'list_id': lid})
    assert r2.status_code == 200
    tid2 = r2.json().get('id')

    # set created_at to today (UTC now)
    created_override = datetime.now(timezone.utc)
    async with async_session() as sess:
        q = await sess.exec(select(Todo).where(Todo.id == tid1))
        t1 = q.first()
        assert t1 is not None
        t1.created_at = created_override
        sess.add(t1)

        q = await sess.exec(select(Todo).where(Todo.id == tid2))
        t2 = q.first()
        assert t2 is not None
        t2.created_at = created_override
        sess.add(t2)

        await sess.commit()

    # build window for the next two years after creation
    next_year = created_override.year + 1
    year_after = created_override.year + 2

    # query occurrences for next_year and ensure the dates appear
    start = datetime(next_year, 1, 1, tzinfo=timezone.utc).isoformat()
    end = datetime(next_year, 12, 31, tzinfo=timezone.utc).isoformat()
    resp = await client.get('/calendar/occurrences', params={'start': start, 'end': end})
    assert resp.status_code == 200
    occ = resp.json().get('occurrences', [])
    days_next = [o['occurrence_dt'][:10] for o in occ if o['item_type'] == 'todo' and o.get('id') in (tid1, tid2)]

    assert f"{next_year}-01-01" in days_next
    assert f"{next_year}-03-05" in days_next

    # query occurrences for the year after next and ensure the dates do NOT appear
    start2 = datetime(year_after, 1, 1, tzinfo=timezone.utc).isoformat()
    end2 = datetime(year_after, 12, 31, tzinfo=timezone.utc).isoformat()
    resp2 = await client.get('/calendar/occurrences', params={'start': start2, 'end': end2})
    assert resp2.status_code == 200
    occ2 = resp2.json().get('occurrences', [])
    days_after = [o['occurrence_dt'][:10] for o in occ2 if o['item_type'] == 'todo' and o.get('id') in (tid1, tid2)]

    assert f"{year_after}-01-01" not in days_after
    assert f"{year_after}-03-05" not in days_after
