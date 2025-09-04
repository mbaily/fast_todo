import pytest
pytestmark = pytest.mark.asyncio


async def test_move_update_delete_todo(client):
    # create a new list
    r = await client.post("/lists", params={"name": "work"})
    assert r.status_code == 200
    work = r.json()
    assert work["name"] == "work"
    work_id = work["id"]

    # create a todo in default list (explicit list required by API)
    # create/get default list
    rdef = await client.post('/lists', params={'name': 'default'})
    dlist = rdef.json()
    r2 = await client.post("/todos", json={"text": "task1", "note": "initial", "list_id": dlist['id']})
    assert r2.status_code == 200
    t = r2.json()
    tid = t["id"]
    default_list_id = t["list_id"]

    # move to work list
    r3 = await client.patch(f"/todos/{tid}", json={"list_id": work_id})
    assert r3.status_code == 200
    moved = r3.json()
    assert moved["list_id"] == work_id

    # update text and note
    r4 = await client.patch(f"/todos/{tid}", json={"text": "task1 updated", "note": "changed"})
    assert r4.status_code == 200
    updated = r4.json()
    assert updated["text"] == "task1 updated"
    assert updated["note"] == "changed"

    # delete todo
    r5 = await client.delete(f"/todos/{tid}")
    assert r5.status_code == 200
    assert r5.json()["ok"] is True

    # get should return 404
    r6 = await client.get(f"/todos/{tid}")
    assert r6.status_code == 404


async def test_multiple_completion_types_behavior(client):
    # ensure a list exists and use it
    rlist = await client.post('/lists', params={'name': 'multi-list'})
    ml = rlist.json()
    r = await client.post("/todos", json={"text": "multi complete", "list_id": ml['id']})
    assert r.status_code == 200
    todo = r.json()
    tid = todo["id"]

    # mark default done
    r2 = await client.post(f"/todos/{tid}/complete", params={"completion_type": "default", "done": True})
    assert r2.status_code == 200
    # add another completion type and mark done
    r3 = await client.post(f"/todos/{tid}/complete", params={"completion_type": "review", "done": True})
    assert r3.status_code == 200

    # now unmark default
    r4 = await client.post(f"/todos/{tid}/complete", params={"completion_type": "default", "done": False})
    assert r4.status_code == 200

    # fetch and assert both types present with correct booleans
    r5 = await client.get(f"/todos/{tid}")
    assert r5.status_code == 200
    got = r5.json()
    comps_field = got.get("completions")
    # Support both dict shape {name: bool} and list-of-dicts [{completion_type_id, done}]
    if isinstance(comps_field, dict):
        values = list(comps_field.values())
        count = len(values)
        any_false = any(v is False for v in values)
        any_true = any(v is True for v in values)
    else:
        values = [c.get("done") for c in (comps_field or [])]
        count = len(values)
        any_false = any(v is False for v in values)
        any_true = any(v is True for v in values)
    # simplest check: there should be at least 2 completion entries
    assert count >= 2
    # ensure that one done is False (default) and one is True (review)
    assert any_false
    assert any_true


async def test_long_unicode_text(client):
    long_text = "🔥" * 5000 + " — Большой текст — 漢字"
    rlist = await client.post('/lists', params={'name': 'longtext-list'})
    l = rlist.json()
    r = await client.post("/todos", json={"text": long_text, 'list_id': l['id']})
    assert r.status_code == 200
    todo = r.json()
    assert todo["text"] == long_text
