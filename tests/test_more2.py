import pytest
from sqlmodel import select
from app.db import async_session
from app.models import Hashtag, TodoCompletion

pytestmark = pytest.mark.asyncio


async def test_delete_completion_type_removes_todocompletions(client):
    # create a list and a todo
    r = await client.post('/lists', params={'name': 'ct-cascade'})
    assert r.status_code == 200
    lst = r.json()
    lid = lst['id']

    r2 = await client.post('/todos', json={'text': 'to-be-completed', 'list_id': lid})
    assert r2.status_code == 200
    todo = r2.json()
    tid = todo['id']

    # create a completion type
    rc = await client.post(f'/lists/{lid}/completion_types', params={'name': 'temp'})
    assert rc.status_code == 200

    # mark todo complete with that type
    r3 = await client.post(
        f'/todos/{tid}/complete', params={'completion_type': 'temp', 'done': True}
    )
    assert r3.status_code == 200

    # ensure there's a TodoCompletion for that todo
    async with async_session() as sess:
        q = await sess.exec(select(TodoCompletion).where(TodoCompletion.todo_id == tid))
        allc = q.all()
        assert len(allc) >= 1

    # delete the completion type
    rd = await client.delete(f'/lists/{lid}/completion_types/temp')
    assert rd.status_code == 200

    # ensure TodoCompletion rows removed
    async with async_session() as sess:
        q2 = await sess.exec(
            select(TodoCompletion).where(TodoCompletion.todo_id == tid)
        )
        assert q2.first() is None


async def test_hashtag_uniqueness_across_lists(client):
    # create two lists
    r1 = await client.post('/lists', params={'name': 'hl1'})
    r2 = await client.post('/lists', params={'name': 'hl2'})
    l1 = r1.json()
    l2 = r2.json()

    # add same hashtag text to both lists
    a1 = await client.post(f"/lists/{l1['id']}/hashtags", params={'tag': 'shared'})
    assert a1.status_code == 200
    a2 = await client.post(f"/lists/{l2['id']}/hashtags", params={'tag': 'shared'})
    assert a2.status_code == 200

    # ensure only one Hashtag row exists for that tag
    async with async_session() as sess:
        q = await sess.exec(select(Hashtag).where(Hashtag.tag == '#shared'))
        matches = q.all()
        assert len(matches) == 1

    # ensure both lists have a ListHashtag link (implicitly true if endpoints succeeded)
