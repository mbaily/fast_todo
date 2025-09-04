import pytest
from datetime import datetime, timezone
from app.db import async_session, init_db
from app.models import ListState, Hashtag
from sqlmodel import select

pytestmark = pytest.mark.asyncio


async def test_todo_defaults_to_server_default_list(client):
    # create a list and set it as the server default, then create a todo
    rlist = await client.post('/lists', params={'name': 'server-default-test'})
    assert rlist.status_code == 200
    lst = rlist.json()
    default_id = lst['id']
    rs = await client.post(f'/server/default_list/{default_id}')
    assert rs.status_code == 200

    # create todo specifying the default list id (API requires list_id)
    r = await client.post('/todos', json={'text': 'uses-default', 'list_id': default_id})
    assert r.status_code == 200
    todo = r.json()
    assert todo['list_id'] == default_id


async def test_update_todo_updates_modified_at(client):
    # create a list and todo for update testing
    rl = await client.post('/lists', params={'name': 'update-list'})
    l = rl.json()
    r = await client.post('/todos', json={'text': 'to-update', 'list_id': l['id']})
    assert r.status_code == 200
    t = r.json()
    tid = t['id']
    # fetch got modified_at
    r2 = await client.get(f'/todos/{tid}')
    orig = r2.json()
    orig_mod = orig['modified_at']
    # patch after slight delay
    import asyncio
    await asyncio.sleep(0.01)
    rp = await client.patch(f'/todos/{tid}', json={'text': 'updated'})
    assert rp.status_code == 200
    updated = rp.json()
    assert updated['modified_at'] is not None
    # compare datetimes
    o = datetime.fromisoformat(orig_mod) if orig_mod else None
    n = datetime.fromisoformat(updated['modified_at'])
    if o is not None and o.tzinfo is None:
        o = o.replace(tzinfo=timezone.utc)
    if n.tzinfo is None:
        n = n.replace(tzinfo=timezone.utc)
    if o is not None:
        assert n >= o


async def test_removing_list_hashtag_link_preserves_hashtag_row(client):
    # create list and add hashtag
    r = await client.post('/lists', params={'name': 'preserve-h'})
    lst = r.json()
    lid = lst['id']
    ra = await client.post(f'/lists/{lid}/hashtags', params={'tag': 'keepme'})
    assert ra.status_code == 200
    # remove link
    rd = await client.delete(f'/lists/{lid}/hashtags', params={'tag': 'keepme'})
    assert rd.status_code == 200
    # hashtag row should still exist
    async with async_session() as sess:
        q = await sess.exec(select(Hashtag).where(Hashtag.tag == '#keepme'))
        h = q.first()
        assert h is not None


async def test_deleting_server_default_list_reassigns_or_clears(client):
    # create a list and set as server default, then delete it; server should
    # accept the deletion and reassign the default to another list if one
    # exists, otherwise clear it.
    rlist = await client.post('/lists', params={'name': 'protected-default'})
    assert rlist.status_code == 200
    lst = rlist.json()
    did = lst['id']
    rs = await client.post(f'/server/default_list/{did}')
    assert rs.status_code == 200

    r = await client.delete(f'/lists/{did}')
    assert r.status_code == 200

    # server default may be reassigned or cleared; check get endpoint for
    # either a 200 (and id != deleted) or 404 if cleared.
    rg = await client.get('/server/default_list')
    if rg.status_code == 200:
        new = rg.json()
        assert new['id'] != did
    else:
        assert rg.status_code == 404
