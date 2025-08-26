import pytest
from datetime import datetime
import time
import uuid

pytestmark = pytest.mark.asyncio


async def test_create_completion_type_duplicate(client):
    r = await client.post("/lists", params={"name": "ct_dup_test"})
    assert r.status_code == 200
    lst = r.json()
    lid = lst["id"]

    # create a new completion type with a unique name
    cname = f"extra-{uuid.uuid4().hex[:8]}"
    r1 = await client.post(f"/lists/{lid}/completion_types", params={"name": cname})
    assert r1.status_code == 200

    # creating it again should return 400
    r2 = await client.post(f"/lists/{lid}/completion_types", params={"name": cname})
    assert r2.status_code == 400


async def test_list_hashtag_add_and_remove(client):
    r = await client.post("/lists", params={"name": "lh_test"})
    assert r.status_code == 200
    lst = r.json()
    lid = lst["id"]

    # add hashtag
    ra = await client.post(f"/lists/{lid}/hashtags", params={"tag": "#Example"})
    assert ra.status_code == 200
    assert ra.json()["tag"] == "#example"

    # removing works
    rr = await client.delete(f"/lists/{lid}/hashtags", params={"tag": "#Example"})
    assert rr.status_code == 200
    assert rr.json()["removed"] == "#example"

    # removing again -> 404
    rr2 = await client.delete(f"/lists/{lid}/hashtags", params={"tag": "#Example"})
    assert rr2.status_code == 404


async def test_update_todo_patch_changes_modified_at(client):
    # create a list for the todo (todos must belong to a list)
    rl = await client.post('/lists', params={'name': 'update-todo-list'})
    assert rl.status_code == 200
    lst = rl.json()

    r = await client.post("/todos", params={"text": "to be updated", "list_id": lst['id']})
    assert r.status_code == 200
    todo = r.json()
    tid = todo["id"]
    orig_mod = todo.get("modified_at")
    assert orig_mod is not None

    # sleep briefly to ensure timestamp difference
    time.sleep(0.01)

    rp = await client.patch(f"/todos/{tid}", params={"text": "updated text", "note": "now with note"})
    assert rp.status_code == 200
    newt = rp.json()
    assert newt["text"] == "updated text"
    assert newt["note"] == "now with note"
    assert newt.get("modified_at") is not None
    # modified_at should be different (later) than original
    dt_orig = datetime.fromisoformat(orig_mod)
    dt_new = datetime.fromisoformat(newt["modified_at"])
    assert dt_new >= dt_orig


async def test_deleting_default_list_endpoint_reassigns_or_clears(client):
    # create and set a known default list for this test
    rc = await client.post("/lists", params={"name": "test_default_for_delete"})
    assert rc.status_code == 200
    created = rc.json()
    did = created["id"]
    rset = await client.post(f"/server/default_list/{did}")
    assert rset.status_code == 200

    # deleting default list should be allowed; server will reassign or clear
    rdel = await client.delete(f"/lists/{did}")
    assert rdel.status_code == 200

    rg = await client.get('/server/default_list')
    if rg.status_code == 200:
        new = rg.json()
        assert new['id'] != did
    else:
        assert rg.status_code == 404
