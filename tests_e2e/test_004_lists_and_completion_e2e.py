import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("i", list(range(200)))
def test_list_priority_and_completion_types(e2e_client, bearer_headers_a, i):
    # Create list and adjust priority and completion types
    r = e2e_client.post("/lists", params={"name": f"E2E-Priority {i}"}, headers=bearer_headers_a)
    assert r.status_code == 200
    lid = r.json()["id"]
    # Patch list priority
    r = e2e_client.patch(f"/lists/{lid}", json={"priority": (i % 10) + 1}, headers=bearer_headers_a)
    assert r.status_code == 200
    # Ensure completion types endpoint works and add a new one
    r = e2e_client.get(f"/lists/{lid}/completion_types", headers=bearer_headers_a)
    assert r.status_code == 200
    r2 = e2e_client.post(f"/lists/{lid}/completion_types", params={"name": f"ct{i%5}"}, headers=bearer_headers_a)
    assert r2.status_code in (200, 400)  # 400 when duplicate name in same list
