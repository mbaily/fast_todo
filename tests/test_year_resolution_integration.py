import pytest
from datetime import datetime, timezone
from sqlmodel import select

from app.db import async_session
from app.models import Todo, ListState


@pytest.mark.asyncio
async def test_calendar_resolution_uses_todo_created_at(client):
    # create a list
    r = await client.post('/lists', params={'name': 'Integration YR List'})
    assert r.status_code == 200
    lid = r.json().get('id')

    # create a todo with yearless date in text
    r = await client.post('/todos', params={'text': 'Event Jan 22', 'list_id': lid})
    assert r.status_code == 200
    todo = r.json()
    tid = todo.get('id')

    # update created_at to late in year (simulate created Dec 20 2025)
    created_override = datetime(2025, 12, 20, tzinfo=timezone.utc)
    async with async_session() as sess:
        q = await sess.exec(select(Todo).where(Todo.id == tid))
        t = q.first()
        assert t is not None
        t.created_at = created_override
        sess.add(t)
    await sess.commit()
    await sess.close()

    # query occurrences for Jan 2026 only
    start = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    end = datetime(2026, 1, 31, tzinfo=timezone.utc).isoformat()
    resp = await client.get('/calendar/occurrences', params={'start': start, 'end': end})
    assert resp.status_code == 200
    occ = resp.json().get('occurrences', [])
    days = [o['occurrence_dt'][:10] for o in occ if o['item_type'] == 'todo' and o['id'] == tid]
    assert '2026-01-22' in days


@pytest.mark.asyncio
async def test_calendar_window_prefers_window_candidates(client):
    # create a list and todo
    r = await client.post('/lists', params={'name': 'Integration Window List'})
    assert r.status_code == 200
    lid = r.json().get('id')
    r = await client.post('/todos', params={'text': 'WindowEvent Jan 22', 'list_id': lid})
    assert r.status_code == 200
    tid = r.json().get('id')

    # created_at earlier (2025-08-01)
    created_override = datetime(2025, 8, 1, tzinfo=timezone.utc)
    async with async_session() as sess:
        q = await sess.exec(select(Todo).where(Todo.id == tid))
        t = q.first()
        t.created_at = created_override
        sess.add(t)
        await sess.commit()

    # query window spanning 2026-2027 and expect both occurrences
    start = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    end = datetime(2027, 12, 31, tzinfo=timezone.utc).isoformat()
    resp = await client.get('/calendar/occurrences', params={'start': start, 'end': end})
    assert resp.status_code == 200
    occ = resp.json().get('occurrences', [])
    days = [o['occurrence_dt'][:10] for o in occ if o['item_type'] == 'todo' and o['id'] == tid]
    assert '2026-01-22' in days
    assert '2027-01-22' in days
