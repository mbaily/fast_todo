import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import init_db, async_session
from app.models import ListState, CompletionType
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


async def test_create_list_has_default_completion(client):
    # create a new list
    r = await client.post("/lists", params={"name": "withdefault"})
    assert r.status_code == 200
    lst = r.json()
    list_id = lst["id"]

    # query DB to ensure CompletionType exists for this list
    async with async_session() as sess:
        q = await sess.exec(select(CompletionType).where(CompletionType.list_id == list_id).where(CompletionType.name == "default"))
        c = q.first()
        assert c is not None


async def test_invalid_hashtag_returns_400_for_list_and_todo(client):
    # ensure DB
    await ensure_db()
    # create list and todo
    r = await client.post("/lists", params={"name": "tagtest"})
    lst = r.json()
    r2 = await client.post("/todos", params={"text": "tagtodo", "list_id": lst['id']})
    todo = r2.json()

    # invalid tags
    bad_tags = ["", "!bad", "bad!tag", "#bad!", "   "]
    for bt in bad_tags:
        rl = await client.post(f"/lists/{lst['id']}/hashtags", params={"tag": bt})
        assert rl.status_code == 400
        rt = await client.post(f"/todos/{todo['id']}/hashtags", params={"tag": bt})
        assert rt.status_code == 400
