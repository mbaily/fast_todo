import pytest
from app.main import app
pytestmark = pytest.mark.asyncio


async def test_create_and_get_todo(client):
    # create a list and post todo into it (API requires list_id)
    rl = await client.post('/lists', params={'name': 'buy-milk-list'})
    lst = rl.json()
    r = await client.post("/todos", json={"text": "buy milk", "list_id": lst['id']})
    assert r.status_code == 200
    todo = r.json()
    todo_id = todo["id"]

    r2 = await client.get(f"/todos/{todo_id}")
    assert r2.status_code == 200
    got = r2.json()
    assert got["text"] == "buy milk"
    assert got["list_id"] is not None


async def test_defer_and_undefer(client):
    rl = await client.post('/lists', params={'name': 'defer-list-2'})
    lst = rl.json()
    r = await client.post("/todos", json={"text": "defer me", "list_id": lst['id']})
    todo = r.json()
    todo_id = todo["id"]
    # defer for 0 hours -> immediate
    r2 = await client.post(f"/todos/{todo_id}/defer", params={"hours": 0})
    assert r2.status_code == 200
    data = r2.json()
    assert data["deferred_until"] is not None
    # undefer via admin
    r3 = await client.post("/admin/undefer")
    assert r3.status_code == 200
    ud = r3.json()
    assert ud["undeferred"] >= 1
    # confirm it's cleared
    r4 = await client.get(f"/todos/{todo_id}")
    assert r4.status_code == 200
    assert r4.json()["deferred_until"] is None


async def test_completion_types_and_mark_done(client):
    rl = await client.post('/lists', params={'name': 'complete-list'})
    lst = rl.json()
    r = await client.post("/todos", json={"text": "complete me", "list_id": lst['id']})
    todo = r.json()
    todo_id = todo["id"]
    r2 = await client.post(f"/todos/{todo_id}/complete", params={"completion_type": "default", "done": True})
    assert r2.status_code == 200
    r3 = await client.get(f"/todos/{todo_id}")
    got = r3.json()
    comps = got.get("completions")
    if isinstance(comps, dict):
        assert any(bool(v) for v in comps.values())
    else:
        assert any(c.get("done") for c in (comps or [])) 
