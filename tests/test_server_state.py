import pytest
from app.db import async_session, init_db
from app.models import ServerState, ListState
from sqlmodel import select

pytestmark = pytest.mark.asyncio


async def test_serverstate_singleton_and_default_exists():
    await init_db()
    async with async_session() as sess:
        qs = await sess.exec(select(ServerState))
        ss = qs.first()
        assert ss is not None
        # if default_list_id set, the list should exist
        if ss.default_list_id is not None:
            ql = await sess.exec(select(ListState).where(ListState.id == ss.default_list_id))
            assert ql.first() is not None


async def test_set_and_get_default_list(client):
    # create a new list
    r = await client.post('/lists', params={'name': 'serverstate-list'})
    assert r.status_code == 200
    lst = r.json()
    lid = lst['id']

    # set as default
    rs = await client.post(f'/server/default_list/{lid}')
    assert rs.status_code == 200
    bg = await client.get('/server/default_list')
    assert bg.status_code == 200
    got = bg.json()
    assert got['id'] == lid


async def test_clearing_server_default_returns_404(client):
    # ensure db and serverstate
    await init_db()
    async with async_session() as sess:
        qs = await sess.exec(select(ServerState))
        ss = qs.first()
        assert ss is not None
        # clear default_list_id
        ss.default_list_id = None
        sess.add(ss)
        await sess.commit()
    # now GET should return 404
    r = await client.get('/server/default_list')
    assert r.status_code == 404
