import pytest

pytestmark = pytest.mark.bulk


def test_completion_types_crud(app_client, auth_headers, make_list):
    lid = make_list("CT List #1")
    # Initially has 'default'
    r0 = app_client.get(f"/lists/{lid}/completion_types", headers=auth_headers)
    assert r0.status_code == 200
    existing = [c.get("name") for c in r0.json()]
    assert "default" in existing
    # Create new type
    r1 = app_client.post(f"/lists/{lid}/completion_types", params={"name": "done"}, headers=auth_headers)
    assert r1.status_code == 200, r1.text
    # Duplicate should 400
    r2 = app_client.post(f"/lists/{lid}/completion_types", params={"name": "done"}, headers=auth_headers)
    assert r2.status_code == 400
    # Delete non-default
    r3 = app_client.delete(f"/lists/{lid}/completion_types/done", headers=auth_headers)
    assert r3.status_code == 200
    # Deleting default should 400
    r4 = app_client.delete(f"/lists/{lid}/completion_types/default", headers=auth_headers)
    assert r4.status_code == 400


def test_completion_types_negative_missing_list(app_client, auth_headers):
    # Non-existent list id
    r = app_client.get("/lists/9999999/completion_types", headers=auth_headers)
    assert r.status_code == 404
