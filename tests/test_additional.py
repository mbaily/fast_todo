import pytest
import asyncio
from sqlmodel import select
from datetime import timedelta
from app.db import async_session
from app.models import Todo, CompletionType, ListState
from app.utils import now_utc

pytestmark = pytest.mark.asyncio


async def test_default_completion_exists_on_list_creation(client):
    r = await client.post('/lists', params={'name': 'extra-default'})
    assert r.status_code == 200
    lst = r.json()
    lid = lst['id']

    r2 = await client.get(f'/lists/{lid}/completion_types')
    assert r2.status_code == 200
    types = r2.json()
    names = [t['name'] if isinstance(t, dict) else getattr(t, 'name', None) for t in types]
    assert 'default' in names


async def test_admin_undefer_clears_due(client):
    # create list and todo
    r = await client.post('/lists', params={'name': 'extra-undefer'})
    assert r.status_code == 200
    lst = r.json()
    lid = lst['id']
    rt = await client.post('/todos', params={'text': 'to-be-undeft', 'list_id': lid})
    assert rt.status_code == 200
    todo = rt.json()
    tid = todo['id']

    # set deferred_until into the past directly in DB
    async with async_session() as sess:
        q = await sess.get(Todo, tid)
        assert q is not None
        q.deferred_until = now_utc() - timedelta(minutes=5)
        sess.add(q)
        await sess.commit()

    # run undefer
    ru = await client.post('/admin/undefer')
    assert ru.status_code == 200
    data = ru.json()
    assert data.get('undeferred', 0) >= 1

    # confirm todo no longer deferred
    gt = await client.get(f'/todos/{tid}')
    assert gt.status_code == 200
    jt = gt.json()
    assert jt.get('deferred_until') is None


async def test_cannot_delete_default_completion_type(client):
    r = await client.post('/lists', params={'name': 'extra-no-delete'})
    assert r.status_code == 200
    lst = r.json()
    lid = lst['id']

    rd = await client.delete(f'/lists/{lid}/completion_types/default')
    assert rd.status_code == 400


async def test_timestamps_are_timezone_aware(client):
    # create a list and a todo in it (server requires existing list_id)
    rl = await client.post('/lists', params={'name': 'tz-list'})
    lst = rl.json()
    r = await client.post('/todos', params={'text': 'tz-test', 'list_id': lst['id']})
    assert r.status_code == 200
    t = r.json()
    # created_at should include timezone offset (UTC)
    ca = t.get('created_at')
    assert ca is not None
    assert ('+00:00' in ca) or ca.endswith('Z')
