import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import init_db, async_session
from app.models import ListState
from sqlmodel import select
from datetime import datetime, timezone

pytestmark = pytest.mark.asyncio


async def ensure_db():
    await init_db()
    async with async_session() as sess:
        res = await sess.exec(select(ListState).where(ListState.name == "default"))
        if not res.first():
            lst = ListState(name="default")
            sess.add(lst)
            await sess.commit()


async def test_delete_nonexistent_todo_returns_404():
    await ensure_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.delete("/todos/999999")
        assert r.status_code == 404


async def test_add_hashtag_to_nonexistent_todo_returns_404():
    await ensure_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/todos/999999/hashtags", params={"tag": "nope"})
        assert r.status_code == 404


async def test_remove_nonexistent_hashtag_from_list_returns_404(ensure_db, client: AsyncClient):
    # create a list
    r = await client.post("/lists", params={"name": "hlist"})
    lst = r.json()
    # try to remove a tag that does not exist
    r2 = await client.delete(
        f"/lists/{lst['id']}/hashtags",
        params={"tag": "missing"},
    )
    assert r2.status_code == 404


async def test_list_state_toggle(ensure_db, client: AsyncClient):
    # use authenticated client fixture
    r = await client.post("/lists", params={"name": "stateful"})
    lst = r.json()
    # toggle expanded and hide_done
    r2 = await client.post(
        f"/lists/{lst['id']}/state",
        params={"expanded": False, "hide_done": True},
    )
    assert r2.status_code == 200
    got = r2.json()
    assert got["expanded"] is False
    assert got["hide_done"] is True


async def test_defer_returns_iso_and_admin_clears_immediate(ensure_db, client: AsyncClient):
    # use authenticated client fixture
    rl = await client.post('/lists', params={'name': 'defer-list'})
    lst = rl.json()
    r = await client.post("/todos", json={"text": "defer-iso", 'list_id': lst['id']})
    todo = r.json()
    tid = todo['id']
    r2 = await client.post(f"/todos/{tid}/defer", params={"hours": 0})
    assert r2.status_code == 200
    d = r2.json()
    assert 'deferred_until' in d
    # ensure it's ISO-parseable and includes timezone or can be treated as UTC
    parsed = datetime.fromisoformat(d['deferred_until'])
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    assert parsed.tzinfo is not None
    # undefer via admin
    r3 = await client.post('/admin/undefer')
    assert r3.status_code == 200
    # fetch todo and confirm cleared
    r4 = await client.get(f"/todos/{tid}")
    assert r4.status_code == 200
    assert r4.json()['deferred_until'] is None


async def test_concurrent_completion_markings(ensure_db, client: AsyncClient):
    # use authenticated client fixture
    rl = await client.post('/lists', params={'name': 'concurrent-list'})
    lst = rl.json()
    r = await client.post('/todos', json={'text': 'concurrent', 'list_id': lst['id']})
    todo = r.json()
    tid = todo['id']
    # mark two different completion types
    r1 = await client.post(f"/todos/{tid}/complete", params={"completion_type": "a", "done": True})
    r2 = await client.post(f"/todos/{tid}/complete", params={"completion_type": "b", "done": True})
    assert r1.status_code == 200
    assert r2.status_code == 200
    r3 = await client.get(f"/todos/{tid}")
    got = r3.json()
    assert len(got['completions']) >= 2
