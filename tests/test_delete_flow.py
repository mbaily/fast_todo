import pytest
from app.auth import create_csrf_token
from app.db import async_session
from app.models import User, TodoCompletion, TodoHashtag, Hashtag, CompletionType, ListState, ListHashtag
from sqlmodel import select


@pytest.mark.asyncio
async def test_html_delete_ok_owner(client):
    resp = await client.post('/lists', params={'name': 'owner-list'})
    assert resp.status_code == 200
    lst = resp.json()
    resp = await client.post('/todos', params={'text': 'todel', 'list_id': lst['id']})
    assert resp.status_code == 200
    todo = resp.json()

    # set cookie auth and csrf
    token_resp = await client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
    token = token_resp.json().get('access_token')
    import pytest
    from app.auth import create_csrf_token
    from app.db import async_session
    from app.models import User, TodoCompletion, TodoHashtag, Hashtag, CompletionType
    from sqlmodel import select


    @pytest.mark.asyncio
    async def test_html_delete_ok_owner(client):
        resp = await client.post('/lists', params={'name': 'owner-list'})
        assert resp.status_code == 200
        lst = resp.json()
        resp = await client.post('/todos', params={'text': 'todel', 'list_id': lst['id']})
        assert resp.status_code == 200
        todo = resp.json()

        # set cookie auth and csrf
        token_resp = await client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
        token = token_resp.json().get('access_token')
        client.cookies.set('access_token', token)
        csrf = create_csrf_token('testuser')
        client.cookies.set('csrf_token', csrf)

        resp = await client.post(f"/html_no_js/todos/{todo['id']}/delete", data={'_csrf': csrf}, follow_redirects=False)
        assert resp.status_code in (302, 303, 200)

        # ensure todo gone
        resp = await client.get(f"/todos/{todo['id']}")
        assert resp.status_code == 404


    @pytest.mark.asyncio
    async def test_html_delete_no_csrf_authenticated(client):
        resp = await client.post('/lists', params={'name': 'csrf-list'})
        lst = resp.json()
        resp = await client.post('/todos', params={'text': 't1', 'list_id': lst['id']})
        todo = resp.json()

        token_resp = await client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
        token = token_resp.json().get('access_token')
        client.cookies.set('access_token', token)
        # do not set csrf token

        resp = await client.post(f"/html_no_js/todos/{todo['id']}/delete", data={}, follow_redirects=False)
        assert resp.status_code == 403


    @pytest.mark.asyncio
    async def test_html_delete_unauthenticated_public_list(client):
        resp = await client.post('/lists', params={'name': 'public-list'})
        lst = resp.json()
        resp = await client.post('/todos', params={'text': 'public-todo', 'list_id': lst['id']})
        todo = resp.json()

        # make list public by clearing owner_id
        async with async_session() as sess:
            q = await sess.exec(select(User).where(User.username == 'testuser'))
            u = q.first()
            ql = await sess.exec(select(CompletionType))
            # fetch list and set owner to None
            from app.models import ListState
            ql2 = await sess.exec(select(ListState).where(ListState.id == lst['id']))
            lobj = ql2.first()
            lobj.owner_id = None
            sess.add(lobj)
            await sess.commit()

        # remove auth headers/cookies
        client.headers.pop('Authorization', None)
        client.cookies.clear()

        resp = await client.post(f"/html_no_js/todos/{todo['id']}/delete", data={}, follow_redirects=False)
        assert resp.status_code in (302, 303, 200)

        # ensure gone
        resp = await client.get(f"/todos/{todo['id']}")
        assert resp.status_code == 404


    @pytest.mark.asyncio
    async def test_html_delete_forbidden_other_user(client):
        # create list as testuser
        resp = await client.post('/lists', params={'name': 'other-list'})
        lst = resp.json()
        resp = await client.post('/todos', params={'text': 'secret', 'list_id': lst['id']})
        todo = resp.json()

        # create another user and set list owner to that user
        async with async_session() as sess:
            other = User(username='other', password_hash='x')
            sess.add(other)
            await sess.commit()
            await sess.refresh(other)
            from app.models import ListState
            ql2 = await sess.exec(select(ListState).where(ListState.id == lst['id']))
            lobj = ql2.first()
            lobj.owner_id = other.id
            sess.add(lobj)
            await sess.commit()

        # ensure client is authenticated as testuser and has csrf
        token_resp = await client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
        token = token_resp.json().get('access_token')
        client.cookies.set('access_token', token)
        csrf = create_csrf_token('testuser')
        client.cookies.set('csrf_token', csrf)

        resp = await client.post(f"/html_no_js/todos/{todo['id']}/delete", data={'_csrf': csrf}, follow_redirects=False)
        assert resp.status_code == 403


    @pytest.mark.asyncio
    async def test_delete_idempotent(client):
        resp = await client.post('/lists', params={'name': 'idem-list'})
        lst = resp.json()
        resp = await client.post('/todos', params={'text': 'idem', 'list_id': lst['id']})
        todo = resp.json()

        token_resp = await client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
        token = token_resp.json().get('access_token')
        client.cookies.set('access_token', token)
        csrf = create_csrf_token('testuser')
        client.cookies.set('csrf_token', csrf)

        resp1 = await client.post(f"/html_no_js/todos/{todo['id']}/delete", data={'_csrf': csrf}, follow_redirects=False)
        assert resp1.status_code in (302, 303, 200)

        resp2 = await client.post(f"/html_no_js/todos/{todo['id']}/delete", data={'_csrf': csrf}, follow_redirects=False)
        assert resp2.status_code == 404


    @pytest.mark.asyncio
    async def test_delete_cleans_completions_hashtags(client):
        # create list and todo
        resp = await client.post('/lists', params={'name': 'clean-list'})
        lst = resp.json()
        resp = await client.post('/todos', params={'text': 'withlinks', 'list_id': lst['id']})
        todo = resp.json()

        # add a completion and hashtag entries linked to this todo
        async with async_session() as sess:
            # ensure completion type exists
            qt = await sess.exec(select(CompletionType).where(CompletionType.list_id == lst['id']).where(CompletionType.name == 'default'))
            ctype = qt.first()
            if not ctype:
                ctype = CompletionType(name='default', list_id=lst['id'])
                sess.add(ctype)
                await sess.commit()
                await sess.refresh(ctype)
            tc = TodoCompletion(todo_id=todo['id'], completion_type_id=ctype.id, done=True)
            sess.add(tc)
            h = Hashtag(tag='tag1')
            sess.add(h)
            await sess.commit()
            await sess.refresh(h)
            th = TodoHashtag(todo_id=todo['id'], hashtag_id=h.id)
            sess.add(th)
            await sess.commit()

        # delete via html endpoint
        token_resp = await client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
        token = token_resp.json().get('access_token')
        client.cookies.set('access_token', token)
        csrf = create_csrf_token('testuser')
        client.cookies.set('csrf_token', csrf)
        resp = await client.post(f"/html_no_js/todos/{todo['id']}/delete", data={'_csrf': csrf}, follow_redirects=False)
        assert resp.status_code in (302, 303, 200)

        # confirm linked rows gone
        async with async_session() as sess:
            qtc = await sess.exec(select(TodoCompletion).where(TodoCompletion.todo_id == todo['id']))
            assert not qtc.first()
            qth = await sess.exec(select(TodoHashtag).where(TodoHashtag.todo_id == todo['id']))
            assert not qth.first()


    @pytest.mark.asyncio
    async def test_html_delete_redirect_default_when_no_referer(client):
        resp = await client.post('/lists', params={'name': 'noref'})
        lst = resp.json()
        resp = await client.post('/todos', params={'text': 'noref-todo', 'list_id': lst['id']})
        todo = resp.json()

        token_resp = await client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
        token = token_resp.json().get('access_token')
        client.cookies.set('access_token', token)
        csrf = create_csrf_token('testuser')
        client.cookies.set('csrf_token', csrf)

        # ensure no Referer header and perform delete
        client.headers.pop('Referer', None)
        resp = await client.post(f"/html_no_js/todos/{todo['id']}/delete", data={'_csrf': csrf}, follow_redirects=False)
        assert resp.status_code in (302, 303)
        # location header should be present
        loc = resp.headers.get('location')
        assert loc is not None


    @pytest.mark.asyncio
    async def test_delete_list_owner_and_forbidden_cases(client):
        # create a list as testuser (fixture authenticates as testuser)
        resp = await client.post('/lists', params={'name': 'del-list'})
        assert resp.status_code == 200
        lst = resp.json()

        # delete as owner should succeed
        resp = await client.delete(f"/lists/{lst['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get('deleted') == lst['id']

        # create a new list and set owner to another user
        resp = await client.post('/lists', params={'name': 'other-owner-list'})
        lst2 = resp.json()
        async with async_session() as sess:
            other = User(username='other2', password_hash='x')
            sess.add(other)
            await sess.commit()
            await sess.refresh(other)
            from app.models import ListState
            ql2 = await sess.exec(select(ListState).where(ListState.id == lst2['id']))
            lobj = ql2.first()
            lobj.owner_id = other.id
            sess.add(lobj)
            await sess.commit()

        # attempt to delete as testuser should be forbidden
        resp = await client.delete(f"/lists/{lst2['id']}")
        assert resp.status_code == 403


    @pytest.mark.asyncio
    async def test_delete_list_cascades_todos_and_deps(client):
        # create list and todo
        resp = await client.post('/lists', params={'name': 'cascade-list'})
        lst = resp.json()
        resp = await client.post('/todos', params={'text': 'casc-todo', 'list_id': lst['id']})
        todo = resp.json()

        # add completion/hashtag linked to this todo
        async with async_session() as sess:
            qt = await sess.exec(select(CompletionType).where(CompletionType.list_id == lst['id']).where(CompletionType.name == 'default'))
            ctype = qt.first()
            if not ctype:
                ctype = CompletionType(name='default', list_id=lst['id'])
                sess.add(ctype)
                await sess.commit()
                await sess.refresh(ctype)
            tc = TodoCompletion(todo_id=todo['id'], completion_type_id=ctype.id, done=True)
            sess.add(tc)
            h = Hashtag(tag='cascade-tag')
            sess.add(h)
            await sess.commit()
            await sess.refresh(h)
            th = TodoHashtag(todo_id=todo['id'], hashtag_id=h.id)
            sess.add(th)
            # add list-hashtag link
            from app.models import ListHashtag
            lh = ListHashtag(list_id=lst['id'], hashtag_id=h.id)
            sess.add(lh)
            await sess.commit()

        # delete the list; server should delete todos and per-todo links
        resp = await client.delete(f"/lists/{lst['id']}")
        assert resp.status_code == 200

        # todo should have been deleted with the list
        resp = await client.get(f"/todos/{todo['id']}")
        assert resp.status_code == 404

        # per-todo completion and hashtag rows should be removed
        async with async_session() as sess:
            qtc = await sess.exec(select(TodoCompletion).where(TodoCompletion.todo_id == todo['id']))
            assert not qtc.first()
            qth = await sess.exec(select(TodoHashtag).where(TodoHashtag.todo_id == todo['id']))
            assert not qth.first()
            # list-hashtag link should be removed
            qlh = await sess.exec(select(ListHashtag).where(ListHashtag.list_id == lst['id']))
            assert not qlh.first()

        # clear auth headers to simulate unauthenticated client
        client.headers.pop('Authorization', None)
        # create a new list owned by another user and ensure unauthenticated deletion is forbidden
        resp = await client.post('/lists', params={'name': 'owned-by-other'})
        owned = resp.json()
        async with async_session() as sess:
            other = User(username='other3', password_hash='x')
            sess.add(other)
            await sess.commit()
            await sess.refresh(other)
            ql3 = await sess.exec(select(ListState).where(ListState.id == owned['id']))
            lobj3 = ql3.first()
            lobj3.owner_id = other.id
            sess.add(lobj3)
            await sess.commit()

        resp = await client.delete(f"/lists/{owned['id']}")
        assert resp.status_code == 403
