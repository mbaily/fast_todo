import pytest
import asyncio
from sqlmodel import select
from app.db import async_session
from app.models import CompletionType, Hashtag, ListHashtag, TodoHashtag

pytestmark = pytest.mark.asyncio

CONCURRENCY = 30


async def run_many(tasks):
    return await asyncio.gather(*tasks)


async def test_concurrent_create_completion_type(client):
    # create a list
    r = await client.post('/lists', params={'name': 'concur-ct'})
    assert r.status_code == 200
    lst = r.json()
    lid = lst['id']

    async def make_ct():
        resp = await client.post(f'/lists/{lid}/completion_types', params={'name': 'concurrent'})
        return resp.status_code

    tasks = [make_ct() for _ in range(CONCURRENCY)]
    results = await run_many(tasks)
    # no server errors
    assert all(code < 500 for code in results)

    # ensure only one completion type exists
    async with async_session() as sess:
        q = await sess.exec(select(CompletionType).where(CompletionType.list_id == lid).where(CompletionType.name == 'concurrent'))
        rows = q.all()
        assert len(rows) == 1


async def test_concurrent_add_list_hashtag(client):
    r = await client.post('/lists', params={'name': 'concur-hlist'})
    assert r.status_code == 200
    lst = r.json()
    lid = lst['id']

    async def add_tag():
        resp = await client.post(f'/lists/{lid}/hashtags', params={'tag': 'race'})
        return resp.status_code

    tasks = [add_tag() for _ in range(CONCURRENCY)]
    results = await run_many(tasks)
    assert all(code < 500 for code in results)

    async with async_session() as sess:
        q = await sess.exec(select(Hashtag).where(Hashtag.tag == '#race'))
        tags = q.all()
        assert len(tags) == 1
        h = tags[0]
        ql = await sess.exec(select(ListHashtag).where(ListHashtag.hashtag_id == h.id).where(ListHashtag.list_id == lid))
        links = ql.all()
        assert len(links) == 1


async def test_concurrent_add_todo_hashtag(client):
    r = await client.post('/lists', params={'name': 'concur-htodo'})
    assert r.status_code == 200
    lst = r.json()
    lid = lst['id']
    rt = await client.post('/todos', params={'text': 'race-todo', 'list_id': lid})
    assert rt.status_code == 200
    todo = rt.json()
    tid = todo['id']

    async def add_tag():
        resp = await client.post(f'/todos/{tid}/hashtags', params={'tag': 'race2'})
        return resp.status_code

    tasks = [add_tag() for _ in range(CONCURRENCY)]
    results = await run_many(tasks)
    assert all(code < 500 for code in results)

    async with async_session() as sess:
        q = await sess.exec(select(Hashtag).where(Hashtag.tag == '#race2'))
        tags = q.all()
        assert len(tags) == 1
        h = tags[0]
        ql = await sess.exec(select(TodoHashtag).where(TodoHashtag.hashtag_id == h.id).where(TodoHashtag.todo_id == tid))
        links = ql.all()
        assert len(links) == 1
