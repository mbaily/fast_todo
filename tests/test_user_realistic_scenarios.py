import pytest
import pytest_asyncio
import asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import init_db, async_session
from app.models import User
from app.auth import pwd_context
from sqlmodel import select


@pytest_asyncio.fixture
async def prepare_db():
    await init_db()
    yield


async def create_user(username: str, password: str):
    async with async_session() as sess:
        ph = pwd_context.hash(password)
        u = User(username=username, password_hash=ph, is_admin=False)
        sess.add(u)
        try:
            await sess.commit()
        except Exception:
            await sess.rollback()
            q = await sess.exec(select(User).where(User.username == username))
            return q.first()
        await sess.refresh(u)
        return u


async def get_token(client: AsyncClient, username: str, password: str):
    r = await client.post("/auth/token", json={"username": username, "password": password})
    return r.json().get("access_token")


@pytest.mark.asyncio
async def test_bulk_todo_workflow(prepare_db):
    """Simulate a user creating a list and many todos, then marking and deleting some."""
    transport = ASGITransport(app=app)
    await create_user("bulkuser", "bulkpass")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        token = await get_token(ac, "bulkuser", "bulkpass")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        # create list
        rl = await client.post("/lists", params={"name": "bulk-list"})
        assert rl.status_code == 200
        lid = rl.json()["id"]

        # create 50 todos quickly
        tasks = [client.post("/todos", params={"text": f"task {i}", "list_id": lid}) for i in range(50)]
        res = await asyncio.gather(*tasks)
        assert all(r.status_code == 200 for r in res)

        # mark every 5th as done and delete every 7th
        created = [r.json()["id"] for r in res]
        mark_tasks = [client.post(f"/todos/{tid}/complete", params={"done": True}) for i, tid in enumerate(created) if i % 5 == 0]
        del_tasks = [client.delete(f"/todos/{tid}") for i, tid in enumerate(created) if i % 7 == 0]
        mres = await asyncio.gather(*mark_tasks)
        dres = await asyncio.gather(*del_tasks)
        assert all(r.status_code == 200 for r in mres)
        assert all(r.status_code == 200 for r in dres)


@pytest.mark.asyncio
async def test_concurrent_user_actions(prepare_db):
    """Two users concurrently create lists and todos to surface race issues."""
    transport = ASGITransport(app=app)
    await create_user("conc1", "p1")
    await create_user("conc2", "p2")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        t1 = await get_token(ac, "conc1", "p1")
        t2 = await get_token(ac, "conc2", "p2")

    async def user_work(token: str, prefix: str):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            c.headers.update({"Authorization": f"Bearer {token}"})
            # create multiple lists
            lists = [await c.post("/lists", params={"name": f"{prefix}-L{i}"}) for i in range(5)]
            assert all(r.status_code == 200 for r in lists)
            # create todos in each list
            for r in lists:
                lid = r.json()["id"]
                tks = [c.post("/todos", params={"text": f"{prefix}-task-{i}", "list_id": lid}) for i in range(10)]
                res = await asyncio.gather(*tks)
                assert all(rr.status_code == 200 for rr in res)

    await asyncio.gather(user_work(t1, "u1"), user_work(t2, "u2"))


@pytest.mark.asyncio
async def test_hashtag_and_removal_flow(prepare_db):
    transport = ASGITransport(app=app)
    await create_user("tagger", "tagpass")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        token = await get_token(ac, "tagger", "tagpass")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        r = await client.post("/lists", params={"name": "tag-list"})
        lid = r.json()["id"]
        # add hashtag to list
        ra = await client.post(f"/lists/{lid}/hashtags", params={"tag": "#work"})
        assert ra.status_code == 200
        # create a todo and tag it
        t = await client.post("/todos", params={"text": "tagged todo", "list_id": lid})
        tid = t.json()["id"]
        rtag = await client.post(f"/todos/{tid}/hashtags", params={"tag": "#work"})
        assert rtag.status_code == 200
        # remove hashtag from list
        rrm = await client.delete(f"/lists/{lid}/hashtags", params={"tag": "#work"})
        assert rrm.status_code == 200


@pytest.mark.asyncio
async def test_long_unicode_and_admin_undefer(prepare_db):
    transport = ASGITransport(app=app)
    await create_user("unicode", "upass")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        token = await get_token(ac, "unicode", "upass")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        r = await client.post("/lists", params={"name": "uni-list"})
        lid = r.json()["id"]
        long_text = "🔥" * 2000 + " — 多言語テキスト — тест"
        rt = await client.post("/todos", params={"text": long_text, "list_id": lid})
        assert rt.status_code == 200
        tid = rt.json()["id"]
        # defer the todo into the past, then run admin undefer
        rd = await client.post(f"/todos/{tid}/defer", params={"hours": -1000})
        assert rd.status_code == 200
        # call admin undefer
        run = await client.post("/admin/undefer")
        assert run.status_code == 200
        # todo should no longer be deferred
        g = await client.get(f"/todos/{tid}")
        assert g.status_code == 200
        assert g.json().get("deferred_until") is None
