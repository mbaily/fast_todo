import pytest
from app.db import init_db, async_session
from app.models import ListState
from sqlmodel import select

pytestmark = pytest.mark.asyncio


async def ensure_db():
    await init_db()
    async with async_session() as sess:
        res = await sess.exec(select(ListState).where(ListState.name == "default"))
        if not res.first():
            lst = ListState(name="default")
            sess.add(lst)
            await sess.commit()


async def test_list_create_and_manage_completion_types(client):
    # create list
    r = await client.post('/lists', params={'name': 'ct-list'})
    assert r.status_code == 200
    lst = r.json()
    lid = lst['id']

    # default completion should already exist
    rct = await client.get(f'/lists/{lid}/completion_types')
    assert rct.status_code == 200
    types = rct.json()
    assert any(t['name'] == 'default' for t in types)

    # create a new completion type
    r2 = await client.post(f'/lists/{lid}/completion_types', params={'name': 'review'})
    assert r2.status_code == 200
    created = r2.json()
    assert created['name'] == 'review'

    # listing shows it
    r3 = await client.get(f'/lists/{lid}/completion_types')
    assert any(t['name'] == 'review' for t in r3.json())

    # cannot create duplicate
    r4 = await client.post(f'/lists/{lid}/completion_types', params={'name': 'review'})
    assert r4.status_code == 400

    # cannot delete default
    r5 = await client.delete(f'/lists/{lid}/completion_types/default')
    assert r5.status_code == 400

    # delete created one
    r6 = await client.delete(f'/lists/{lid}/completion_types/review')
    assert r6.status_code == 200
    assert r6.json()['deleted'] == 'review'
