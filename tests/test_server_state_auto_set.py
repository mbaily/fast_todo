import pytest
from app.db import async_session, init_db
from app.models import ServerState, ListState
from sqlmodel import select

pytestmark = pytest.mark.asyncio


async def test_create_list_sets_server_default_when_unset(client):
    # ensure server default cleared
    await init_db()
    async with async_session() as sess:
        qs = await sess.exec(select(ServerState))
        ss = qs.first()
        assert ss is not None
        ss.default_list_id = None
        sess.add(ss)
        await sess.commit()

    # create a new list
    r = await client.post('/lists', params={'name': 'auto-default-list'})
    assert r.status_code == 200
    lst = r.json()

    # server default should now point to this list
    rg = await client.get('/server/default_list')
    assert rg.status_code == 200
    got = rg.json()
    assert got['id'] == lst['id']
