import pytest
from httpx import AsyncClient
from app.auth import create_csrf_token
from sqlmodel import select
from app.models import ListState, ListTrashMeta


@pytest.mark.asyncio
async def test_list_delete_moves_to_trash_and_restore(client: AsyncClient):
    # login and set cookies for CSRF and authorization
    token_resp = await client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
    assert token_resp.status_code == 200
    token = token_resp.json().get('access_token')
    client.cookies.set('access_token', token)
    client.headers.update({'Authorization': f'Bearer {token}'})
    csrf = create_csrf_token('testuser')
    client.cookies.set('csrf_token', csrf)

    # create a parent list and a sublist with a todo
    resp = await client.post('/lists', params={'name': 'list-trash-src'})
    assert resp.status_code == 200
    parent = resp.json()
    # create sublist via HTML endpoint (requires CSRF)
    resp = await client.post(
        f"/html_no_js/lists/{parent['id']}/sublists/create",
        data={'name': 'subl', '_csrf': csrf}
    )
    assert resp.status_code in (303, 302, 200)
    # find the created sublist id
    from app.db import async_session
    from sqlmodel import select
    async with async_session() as sess:
        q = await sess.exec(
            select(ListState)
            .where(ListState.parent_list_id == parent['id'])
            .where(ListState.name == 'subl')
        )
        sub = q.first()
        assert sub is not None
        # create a todo in sublist
    resp = await client.post('/todos', json={'text': 'sub todo', 'list_id': sub.id})
    assert resp.status_code == 200
    _ = resp.json()

    # login and set cookies for CSRF and authorization
    token_resp = await client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
    assert token_resp.status_code == 200
    token = token_resp.json().get('access_token')
    client.cookies.set('access_token', token)
    client.headers.update({'Authorization': f'Bearer {token}'})
    csrf = create_csrf_token('testuser')
    client.cookies.set('csrf_token', csrf)

    # delete the sublist via HTML (should move to Trash)
    resp = await client.post(f'/html_no_js/lists/{sub.id}/delete', data={'_csrf': csrf})
    assert resp.status_code in (303,302,200)

    # verify ListTrashMeta created and parent changed to Trash
    async with async_session() as sess:
        # ensure we fetch the Trash list for the authenticated test user
        uq = await sess.exec(
            select(ListState.owner_id).where(ListState.id == parent['id'])
        )
        owner_id = uq.first()
        owner_id = owner_id[0] if isinstance(owner_id, (tuple, list)) else owner_id
        q = await sess.exec(
            select(ListState)
            .where(ListState.name == 'Trash')
            .where(ListState.owner_id == owner_id)
        )
        trash = q.first()
        assert trash is not None
        q2 = await sess.exec(
            select(ListState)
            .where(ListState.parent_list_id == trash.id)
            .where(ListState.id == sub.id)
        )
        srow = q2.first()
        assert srow is not None
        q3 = await sess.exec(
            select(ListTrashMeta).where(ListTrashMeta.list_id == sub.id)
        )
        meta = q3.first()
        assert meta is not None
        assert meta.original_parent_list_id == parent['id']

    # view trash page
    resp = await client.get('/html_no_js/trash')
    assert resp.status_code == 200
    assert 'Trashed lists' in resp.text
    assert 'subl' in resp.text

    # restore sublist
    resp = await client.post(
        f'/html_no_js/trash/lists/{sub.id}/restore',
        data={'_csrf': csrf},
        follow_redirects=False,
    )
    assert resp.status_code in (303,302,200)

    # verify parent restored
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.id == sub.id))
        srow = q.first()
        assert srow is not None
        assert srow.parent_list_id == parent['id']


@pytest.mark.asyncio
async def test_permanent_delete_list_from_trash(client: AsyncClient):
    # create a list
    # login and set cookies
    token_resp = await client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
    assert token_resp.status_code == 200
    token = token_resp.json().get('access_token')
    client.cookies.set('access_token', token)
    client.headers.update({'Authorization': f'Bearer {token}'})
    csrf = create_csrf_token('testuser')
    client.cookies.set('csrf_token', csrf)
    # create a list as testuser
    resp = await client.post('/lists', params={'name': 'list-trash-src-2'})
    lst = resp.json()

    # move to trash
    resp = await client.post(
        f"/html_no_js/lists/{lst['id']}/delete",
        data={'_csrf': csrf},
    )
    assert resp.status_code in (303,302,200)

    # permanently delete from trash
    resp = await client.post(
        f"/html_no_js/trash/lists/{lst['id']}/delete",
        data={'_csrf': csrf},
        follow_redirects=False,
    )
    assert resp.status_code in (303,302,200)

    # confirm tombstone for the list
    from app.db import async_session
    from app.models import Tombstone
    async with async_session() as sess:
        q = await sess.exec(
            select(Tombstone)
            .where(Tombstone.item_type == 'list')
            .where(Tombstone.item_id == lst['id'])
        )
        ts = q.first()
        assert ts is not None
