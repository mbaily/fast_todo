import pytest
from app.db import async_session, init_db
from app.models import ServerState, ListState
from sqlmodel import select

pytestmark = pytest.mark.asyncio


async def test_set_default_to_nonexistent_returns_404(client):
    r = await client.post('/server/default_list/999999')
    assert r.status_code == 404


async def test_delete_list_reassigns_todos_to_server_default(client):
    # create two lists: other (will become default) and temp (to delete)
    r_other = await client.post('/lists', params={'name': 'other-default'})
    assert r_other.status_code == 200
    other = r_other.json()
    r_temp = await client.post('/lists', params={'name': 'temp-to-delete'})
    assert r_temp.status_code == 200
    temp = r_temp.json()

    # create a todo in temp
    rt = await client.post('/todos', json={'text': 'task-temp', 'list_id': temp['id']})
    assert rt.status_code == 200
    todo = rt.json()

    # set server default to other
    rs = await client.post(f"/server/default_list/{other['id']}")
    assert rs.status_code == 200

    # delete temp list; todos must not be moved by server
    rd = await client.delete(f"/lists/{temp['id']}")
    assert rd.status_code == 200
    body = rd.json()
    assert body.get('deleted') == temp['id']

    # the todo should have been deleted along with its list
    rget = await client.get(f"/todos/{todo['id']}")
    assert rget.status_code == 404


async def test_clearing_default_allows_deleting_list_and_reassigns(client):
    # create two lists A and B
    ra = await client.post('/lists', params={'name': 'A-delete'})
    rb = await client.post('/lists', params={'name': 'B-receive'})
    a = ra.json(); b = rb.json()

    # set default to A
    rset = await client.post(f"/server/default_list/{a['id']}")
    assert rset.status_code == 200

    # create a todo in A
    rt = await client.post('/todos', json={'text': 'todoA', 'list_id': a['id']})
    todo = rt.json()

    # clear server default manually in DB
    await init_db()
    async with async_session() as sess:
        qs = await sess.exec(select(ServerState))
        ss = qs.first()
        ss.default_list_id = None
        sess.add(ss)
        await sess.commit()

    # now deleting A should be allowed; server should not move todos
    rd = await client.delete(f"/lists/{a['id']}")
    assert rd.status_code == 200
    body = rd.json()
    assert body.get('deleted') == a['id']

    # the todo should have been deleted along with its list
    rget = await client.get(f"/todos/{todo['id']}")
    assert rget.status_code == 404
