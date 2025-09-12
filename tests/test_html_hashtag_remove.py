import pytest
from app.auth import create_csrf_token

@pytest.mark.asyncio
async def test_html_remove_todo_hashtag(ensure_db, client):
    # use the shared ensure_db fixture from conftest to initialize the DB
    # create a list
    r = await client.post('/lists', params={'name': 'Test list'})
    assert r.status_code == 200
    lst = r.json()
    lid = lst['id']
    # create a todo in the list
    r2 = await client.post('/todos', json={'text': 'with #tag', 'note': 'note', 'list_id': lid})
    assert r2.status_code == 200
    todo = r2.json()
    tid = todo['id']
    # add a hashtag via API
    ra = await client.post(f'/todos/{tid}/hashtags', params={'tag': '#tag'})
    assert ra.status_code == 200
    # ensure tag link exists
    from app.models import TodoHashtag, Hashtag
    from sqlmodel import select
    from app.db import async_session
    async with async_session() as sess:
        qh = await sess.exec(select(Hashtag).where(Hashtag.tag == '#tag'))
        h = qh.first()
        assert h is not None
        ql = await sess.exec(
            select(TodoHashtag)
            .where(TodoHashtag.todo_id == tid)
            .where(TodoHashtag.hashtag_id == h.id)
        )
        link = ql.first()
        assert link is not None
    # perform HTML remove using CSRF token
    csrf = create_csrf_token('testuser')
    form = {'_csrf': csrf, 'tag': '#tag'}
    rr = await client.post(
        f'/html_no_js/todos/{tid}/hashtags/remove',
        data=form,
        headers={'referer': f'/html_no_js/todos/{tid}'},
    )
    # should redirect back to the todo page
    assert rr.status_code in (303, 200)
    # now ensure the link row is gone
    from app.db import async_session
    async with async_session() as sess:
        ql2 = await sess.exec(select(TodoHashtag).where(TodoHashtag.todo_id == tid))
        remaining = ql2.all()
        assert len(remaining) == 0


@pytest.mark.asyncio
async def test_html_remove_todo_hashtag_requires_csrf(ensure_db, client):
    # create a list and todo, add a hashtag
    r = await client.post('/lists', params={'name': 'CSRF list'})
    assert r.status_code == 200
    lid = r.json()['id']
    r2 = await client.post('/todos', json={'text': 'with #x', 'note': 'note', 'list_id': lid})
    assert r2.status_code == 200
    tid = r2.json()['id']
    ra = await client.post(f'/todos/{tid}/hashtags', params={'tag': '#x'})
    assert ra.status_code == 200

    # missing CSRF token should be rejected
    rr = await client.post(
        f'/html_no_js/todos/{tid}/hashtags/remove',
        data={'tag': '#x'},
        headers={'referer': f'/html_no_js/todos/{tid}'},
    )
    assert rr.status_code == 403

    # invalid CSRF token (wrong subject) should also be rejected
    bad = create_csrf_token('nobody')
    rr2 = await client.post(
        f'/html_no_js/todos/{tid}/hashtags/remove',
        data={'_csrf': bad, 'tag': '#x'},
        headers={'referer': f'/html_no_js/todos/{tid}'},
    )
    assert rr2.status_code == 403
