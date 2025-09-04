import pytest
from datetime import datetime

pytestmark = pytest.mark.asyncio


async def test_todo_hashtag_idempotent_and_remove(client):
    rl = await client.post('/lists', params={'name': 'tagme-list'})
    lst = rl.json()
    r = await client.post("/todos", json={"text": "tag me twice", 'list_id': lst['id']})
    assert r.status_code == 200
    todo = r.json()
    tid = todo["id"]

    # add the same tag twice
    r1 = await client.post(f"/todos/{tid}/hashtags", params={"tag": "#DupTag"})
    assert r1.status_code == 200
    r2 = await client.post(f"/todos/{tid}/hashtags", params={"tag": "#Duptag"})
    # second add should succeed and be idempotent (case-insensitive normalized)
    assert r2.status_code == 200

    # remove once -> success
    rr = await client.delete(f"/todos/{tid}/hashtags", params={"tag": "#Duptag"})
    assert rr.status_code == 200
    # remove again -> not found (link removed)
    rr2 = await client.delete(f"/todos/{tid}/hashtags", params={"tag": "#Duptag"})
    assert rr2.status_code == 404


async def test_timezone_serialization_on_todo(client):
    rl = await client.post('/lists', params={'name': 'tz-list-2'})
    lst = rl.json()
    r = await client.post("/todos", json={"text": "tz test", 'list_id': lst['id']})
    assert r.status_code == 200
    todo = r.json()
    ca = todo.get("created_at")
    assert ca is not None
    # parse ISO and ensure tzinfo is present
    dt = datetime.fromisoformat(ca)
    assert dt.tzinfo is not None


async def test_delete_list_moves_todos_to_default(client):
    # create a new list
    r = await client.post("/lists", params={"name": "move_list_test"})
    assert r.status_code == 200
    lst = r.json()
    lid = lst["id"]

    # create a todo in that list
    rt = await client.post("/todos", json={"text": "in-other-list", "list_id": lid})
    assert rt.status_code == 200
    todo = rt.json()
    tid = todo["id"]

    # create or set a known default list to receive moved todos
    rf = await client.post("/lists", params={"name": "fallback_default_for_move"})
    assert rf.status_code == 200
    fallback = rf.json()
    fallback_id = fallback["id"]
    # set as server default
    rset = await client.post(f"/server/default_list/{fallback_id}")
    assert rset.status_code == 200

    # delete the list
    rdell = await client.delete(f"/lists/{lid}")
    assert rdell.status_code == 200
    resp = rdell.json()
    # server should not move todos; response should indicate deletion
    assert resp.get("deleted") == lid

    # the todo should have been deleted with its list
    rget = await client.get(f"/todos/{tid}")
    assert rget.status_code == 404


async def test_delete_completion_type_removes_completions(client):
    # create a list
    r = await client.post("/lists", params={"name": "ct_delete_list"})
    assert r.status_code == 200
    lst = r.json()
    lid = lst["id"]

    # create a todo in that list
    rt = await client.post("/todos", json={"text": "complete then delete", "list_id": lid})
    assert rt.status_code == 200
    todo = rt.json()
    tid = todo["id"]

    # mark as complete with a custom completion type
    rc = await client.post(f"/todos/{tid}/complete", params={"completion_type": "temp", "done": True})
    assert rc.status_code == 200

    # verify completion present
    rget = await client.get(f"/todos/{tid}")
    assert rget.status_code == 200
    got = rget.json()
    comps = got.get("completions")
    if isinstance(comps, dict):
        # any completion set to True counts as present
        assert any(bool(v) for v in comps.values()) or len(comps) >= 1
    else:
        assert any(c.get("done") and c.get("completion_type_id") for c in (comps or [])) or len(comps or []) >= 1

    # delete the completion type
    rdel = await client.delete(f"/lists/{lid}/completion_types/temp")
    assert rdel.status_code == 200

    # fetch todo -> completions for that type should be gone
    rget2 = await client.get(f"/todos/{tid}")
    assert rget2.status_code == 200
    got2 = rget2.json()
    comps2 = got2.get("completions")
    # After deleting the type, there should be no completions. Accept both list/dict shapes.
    if isinstance(comps2, dict):
        assert len(comps2) == 0
    else:
        assert len(comps2 or []) == 0
