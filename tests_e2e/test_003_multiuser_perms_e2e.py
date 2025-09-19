import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("i", list(range(200)))
def test_permissions_between_users(e2e_client, bearer_headers_a, bearer_headers_b, i):
    # User A creates list
    r = e2e_client.post("/lists", params={"name": f"A's list {i}"}, headers=bearer_headers_a)
    assert r.status_code == 200
    lid = r.json()["id"]
    # User B attempts to modify A's list hashtags → 403
    r2 = e2e_client.post(f"/lists/{lid}/hashtags", params={"tag": "#x"}, headers=bearer_headers_b)
    assert r2.status_code == 403
    # User A can modify
    r3 = e2e_client.post(f"/lists/{lid}/hashtags", params={"tag": "#ok"}, headers=bearer_headers_a)
    assert r3.status_code == 200
