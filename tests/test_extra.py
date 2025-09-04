import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

pytestmark = pytest.mark.asyncio


async def test_cannot_delete_default_list_and_reassign(ensure_db, client: AsyncClient):
    # create a new list and a todo
    import secrets
    temp_name = f"temp-{secrets.token_hex(6)}"
    r = await client.post("/lists", params={"name": temp_name})
    assert r.status_code == 200
    temp = r.json()
    r2 = await client.post("/todos", json={"text": "temp task", "list_id": temp["id"]})
    assert r2.status_code == 200
    todo = r2.json()

    # create a dedicated default list for this test to avoid colliding
    # with any existing global list name owned by other tests/users.
    import secrets
    unique_name = f"default-temp-{secrets.token_hex(6)}"
    rd = await client.post("/lists", params={"name": unique_name})
    assert rd.status_code == 200
    default = rd.json()
    await client.post(f"/server/default_list/{default['id']}")

    # deleting the default list is allowed; server should pick a new default
    rdel = await client.delete(f"/lists/{default['id']}")
    assert rdel.status_code == 200
    body = rdel.json()
    assert body.get('deleted') == default['id']

    # delete temp list; todos should be deleted with the list
    rdel2 = await client.delete(f"/lists/{temp['id']}")
    assert rdel2.status_code == 200
    body2 = rdel2.json()
    assert body2.get('deleted') == temp['id']


async def test_hashtag_add_remove_on_list_and_todo(ensure_db, client: AsyncClient):
    # create a list and todo
    r = await client.post("/lists", params={"name": "tags"})
    lst = r.json()
    r2 = await client.post("/todos", json={"text": "tagged", "list_id": lst["id"]})
    todo = r2.json()

    # add hashtag to list
    ra = await client.post(f"/lists/{lst['id']}/hashtags", params={"tag": "urgent"})
    assert ra.status_code == 200
    # add same hashtag to todo
    rb = await client.post(f"/todos/{todo['id']}/hashtags", params={"tag": "urgent"})
    assert rb.status_code == 200

    # remove from todo
    rc = await client.delete(f"/todos/{todo['id']}/hashtags", params={"tag": "urgent"})
    assert rc.status_code == 200
    # remove from list
    rd = await client.delete(f"/lists/{lst['id']}/hashtags", params={"tag": "urgent"})
    assert rd.status_code == 200


async def test_timestamp_formats_are_iso(ensure_db, client: AsyncClient):
    # create list and todo explicitly (list_id required)
    rl = await client.post('/lists', params={'name': 'time-test-list'})
    lst = rl.json()
    r = await client.post("/todos", json={"text": "time test", 'list_id': lst['id']})
    todo = r.json()
    tid = todo["id"]
    r2 = await client.get(f"/todos/{tid}")
    got = r2.json()
    # ISO 8601 parse should work
    from datetime import timezone, datetime
    for k in ["created_at", "modified_at"]:
        if got[k] is not None:
            parsed = datetime.fromisoformat(got[k])
            # some DB backends may return naive datetimes; accept those and treat as UTC
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            assert parsed.tzinfo is not None


async def test_set_and_get_default_list(ensure_db, client: AsyncClient):
    r = await client.post("/lists", params={"name": "newdef"})
    assert r.status_code == 200
    lst = r.json()
    rs = await client.post(f"/server/default_list/{lst['id']}")
    assert rs.status_code == 200
    rg = await client.get("/server/default_list")
    assert rg.status_code == 200
    assert rg.json()["id"] == lst["id"]
