import pytest
from httpx import AsyncClient
from app.auth import create_csrf_token
from sqlmodel import select
from app.models import ListState, TrashMeta, Todo


@pytest.mark.asyncio
async def test_delete_moves_to_trash_and_restore(client: AsyncClient):
    # create a list
    resp = await client.post('/lists', params={'name': 'trash-src'})
    assert resp.status_code == 200
    lst = resp.json()
    # create a todo in that list
    resp = await client.post('/todos', json={'text': 'trash me', 'list_id': lst['id']})
    assert resp.status_code == 200
    todo = resp.json()

    # obtain token and set cookies for CSRF using testuser
    token_resp = await client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
    assert token_resp.status_code == 200
    token = token_resp.json().get('access_token')
    assert token
    client.cookies.set('access_token', token)
    csrf = create_csrf_token('testuser')
    client.cookies.set('csrf_token', csrf)

    # call HTML delete endpoint (should move to Trash)
    resp = await client.post(f"/html_no_js/todos/{todo['id']}/delete", data={'_csrf': csrf})
    assert resp.status_code in (303, 302, 200)

    # query server DB to check TrashMeta and list membership
    from app.db import async_session
    async with async_session() as sess:
        # fetch Trash for the authenticated test user
        from app.models import User
        uq = await sess.exec(select(User).where(User.username == 'testuser'))
        u = uq.first()
        q = await sess.exec(select(ListState).where(ListState.name == 'Trash').where(ListState.owner_id == u.id))
        trash = q.first()
        assert trash is not None
    q2 = await sess.exec(select(Todo).where(Todo.list_id == trash.id).where(Todo.id == todo['id']))
    trow = q2.first()
    assert trow is not None
    q3 = await sess.exec(select(TrashMeta).where(TrashMeta.todo_id == todo['id']))
    tm = q3.first()
    assert tm is not None
    orig = tm.original_list_id
    assert orig == lst['id']

    # view trash page
    resp = await client.get('/html_no_js/trash')
    assert resp.status_code == 200
    assert 'Trash' in resp.text
    assert 'trash me' in resp.text

    # restore the todo
    resp = await client.post(f"/html_no_js/trash/{todo['id']}/restore", data={'_csrf': csrf}, follow_redirects=False)
    assert resp.status_code in (303, 302, 200)

    # verify todo returned to original list
    async with async_session() as sess:
        q = await sess.exec(select(Todo).where(Todo.id == todo['id']))
        trow = q.first()
        assert trow is not None
        assert trow.list_id == lst['id']


@pytest.mark.asyncio
async def test_permanent_delete_from_trash(client: AsyncClient):
    # create a list and todo
    resp = await client.post('/lists', params={'name': 'trash-src-2'})
    lst = resp.json()
    resp = await client.post('/todos', json={'text': 'trash perm', 'list_id': lst['id']})
    todo = resp.json()

    # obtain token and set cookies for CSRF using testuser
    token_resp = await client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
    assert token_resp.status_code == 200
    token = token_resp.json().get('access_token')
    assert token
    client.cookies.set('access_token', token)
    csrf = create_csrf_token('testuser')
    client.cookies.set('csrf_token', csrf)

    # move to trash
    resp = await client.post(f'/html_no_js/todos/{todo["id"]}/delete', data={'_csrf': csrf})
    assert resp.status_code in (303,302,200)

    # now permanently delete from trash
    resp = await client.post(f'/html_no_js/trash/{todo["id"]}/delete', data={'_csrf': csrf}, follow_redirects=False)
    assert resp.status_code in (303,302,200)

    from app.db import async_session
    from app.models import Tombstone
    async with async_session() as sess:
        q = await sess.exec(select(Tombstone).where(Tombstone.item_type == 'todo').where(Tombstone.item_id == todo['id']))
        ts = q.first()
        assert ts is not None
