import random
import string
import pytest

pytestmark = pytest.mark.e2e


def _name(i):
    return f"E2E List {i} " + ''.join(random.choice(string.ascii_letters) for _ in range(5))


@pytest.mark.parametrize("i", list(range(200)))
def test_crud_journey(e2e_client, bearer_headers_a, i):
    # create list
    r = e2e_client.post("/lists", params={"name": _name(i)}, headers=bearer_headers_a)
    assert r.status_code == 200, r.text
    lid = r.json()["id"]
    # add hashtag
    r = e2e_client.post(f"/lists/{lid}/hashtags", params={"tag": "#e2e"}, headers=bearer_headers_a)
    assert r.status_code == 200
    # create todo with date
    r = e2e_client.post("/todos", json={"text": "Meet 2025-09-19", "list_id": lid}, headers=bearer_headers_a)
    assert r.status_code == 200
    tid = r.json()["id"]
    # patch todo
    r = e2e_client.patch(f"/todos/{tid}", json={"priority": 5}, headers=bearer_headers_a)
    assert r.status_code == 200
    # get list todos
    r = e2e_client.get(f"/lists/{lid}/todos", headers=bearer_headers_a)
    assert r.status_code == 200
    # remove hashtag
    r = e2e_client.delete(f"/lists/{lid}/hashtags", params={"tag": "#e2e"}, headers=bearer_headers_a)
    assert r.status_code == 200
