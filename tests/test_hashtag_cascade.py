import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.db import async_session
from sqlmodel import select

pytestmark = pytest.mark.asyncio


async def test_deleting_todo_removes_todohashtag_but_preserves_hashtag(ensure_db, client):
    # create a list and a todo
    r = await client.post('/lists', params={'name': 'hc-list'})
    assert r.status_code == 200
    lst = r.json()
    rt = await client.post('/todos', params={'text': 'hc-todo', 'list_id': lst['id']})
    assert rt.status_code == 200
    todo = rt.json()

    # add same hashtag to list and todo
    ra = await client.post(f"/lists/{lst['id']}/hashtags", params={'tag': 'urgent'})
    assert ra.status_code == 200
    rb = await client.post(f"/todos/{todo['id']}/hashtags", params={'tag': 'urgent'})
    assert rb.status_code == 200

    # verify DB links exist
    async with async_session() as sess:
        from app.models import Hashtag, TodoHashtag, ListHashtag
        from app.utils import normalize_hashtag
        qh = await sess.exec(select(Hashtag).where(Hashtag.tag == normalize_hashtag('urgent')))
        h = qh.first()
        assert h is not None
        qtl = await sess.exec(select(TodoHashtag).where(TodoHashtag.todo_id == todo['id']).where(TodoHashtag.hashtag_id == h.id))
        assert qtl.first() is not None
        qll = await sess.exec(select(ListHashtag).where(ListHashtag.list_id == lst['id']).where(ListHashtag.hashtag_id == h.id))
        assert qll.first() is not None

    # delete the todo
    dd = await client.delete(f"/todos/{todo['id']}")
    assert dd.status_code == 200

    # links from the todo should be removed; hashtag row should remain; list->hashtag should remain
    async with async_session() as sess:
        from app.models import Hashtag, TodoHashtag, ListHashtag
        from app.utils import normalize_hashtag
        qh2 = await sess.exec(select(Hashtag).where(Hashtag.tag == normalize_hashtag('urgent')))
        h2 = qh2.first()
        assert h2 is not None
        qtl2 = await sess.exec(select(TodoHashtag).where(TodoHashtag.todo_id == todo['id']))
        assert qtl2.first() is None
        qll2 = await sess.exec(select(ListHashtag).where(ListHashtag.list_id == lst['id']).where(ListHashtag.hashtag_id == h2.id))
        assert qll2.first() is not None


async def test_deleting_list_removes_links_but_preserves_hashtags(ensure_db, client):
    # create a list and two todos
    r = await client.post('/lists', params={'name': 'hc-list-2'})
    assert r.status_code == 200
    lst = r.json()
    t1 = (await client.post('/todos', params={'text': 't1', 'list_id': lst['id']})).json()
    t2 = (await client.post('/todos', params={'text': 't2', 'list_id': lst['id']})).json()

    # add hashtags to list and both todos
    await client.post(f"/lists/{lst['id']}/hashtags", params={'tag': 'alpha'})
    await client.post(f"/todos/{t1['id']}/hashtags", params={'tag': 'alpha'})
    await client.post(f"/todos/{t2['id']}/hashtags", params={'tag': 'beta'})

    # verify links exist
    async with async_session() as sess:
        from app.models import Hashtag, TodoHashtag, ListHashtag
        from app.utils import normalize_hashtag
        nalpha = normalize_hashtag('alpha')
        nbeta = normalize_hashtag('beta')
        qh = await sess.exec(select(Hashtag).where(Hashtag.tag.in_([nalpha, nbeta])))
        tags = [h.tag for h in qh.all()]
        assert nalpha in tags and nbeta in tags

        qtl1 = await sess.exec(select(TodoHashtag).where(TodoHashtag.todo_id == t1['id']))
        assert qtl1.first() is not None
        qtl2 = await sess.exec(select(TodoHashtag).where(TodoHashtag.todo_id == t2['id']))
        assert qtl2.first() is not None
        qll = await sess.exec(select(ListHashtag).where(ListHashtag.list_id == lst['id']))
        assert qll.first() is not None

    # delete the list (should cascade-delete todos and link rows)
    rd = await client.delete(f"/lists/{lst['id']}")
    assert rd.status_code == 200

    # after deletion: hashtags themselves remain, but list and todo hashtag links are gone
    async with async_session() as sess:
        from app.models import Hashtag, TodoHashtag, ListHashtag, Todo
        from app.utils import normalize_hashtag
        nalpha = normalize_hashtag('alpha')
        nbeta = normalize_hashtag('beta')
        qh2 = await sess.exec(select(Hashtag).where(Hashtag.tag.in_([nalpha, nbeta])))
        remaining = [h.tag for h in qh2.all()]
        assert nalpha in remaining and nbeta in remaining

        # no todos for that list should remain
        qtodos = await sess.exec(select(Todo).where(Todo.list_id == lst['id']))
        assert qtodos.first() is None

        # all TodoHashtag entries for those todo ids should be gone
        qth = await sess.exec(select(TodoHashtag).where(TodoHashtag.hashtag_id != None))
        for row in qth.all():
            assert row.todo_id not in (t1['id'], t2['id'])

        # no ListHashtag entries for the deleted list
        qlh = await sess.exec(select(ListHashtag).where(ListHashtag.list_id == lst['id']))
        assert qlh.first() is None
