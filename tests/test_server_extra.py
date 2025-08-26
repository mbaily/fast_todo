import pytest
from app.main import app

pytestmark = pytest.mark.asyncio


async def test_hashtag_invalid(client):
    # create a list first
    rl = await client.post('/lists', params={'name': 'ht-list'})
    assert rl.status_code == 200
    lst = rl.json()
    r = await client.post("/todos", params={"text": "tag me", "list_id": lst['id']})
    assert r.status_code == 200
    todo = r.json()
    tid = todo["id"]

    # invalid hashtag (contains space) should return 400
    r2 = await client.post(f"/todos/{tid}/hashtags", params={"tag": "bad tag"})
    assert r2.status_code == 400


async def test_create_list_idempotent(client):
    # create a new list
    r = await client.post("/lists", params={"name": "dup_list_test"})
    assert r.status_code == 200
    l1 = r.json()
    r2 = await client.post("/lists", params={"name": "dup_list_test"})
    assert r2.status_code == 200
    l2 = r2.json()
    # under the new semantics duplicate names are allowed; should create
    # two distinct lists.
    assert l1["id"] != l2["id"]


async def test_cannot_delete_default_completion(client):
    # create a dedicated list
    r = await client.post("/lists", params={"name": "cannot_del_default"})
    assert r.status_code == 200
    lst = r.json()
    lid = lst["id"]

    # attempt to delete the 'default' completion type should be rejected
    resp = await client.delete(f"/lists/{lid}/completion_types/default")
    assert resp.status_code == 400
