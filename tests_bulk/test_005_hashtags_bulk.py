import pytest

pytestmark = pytest.mark.bulk


def test_add_and_get_list_hashtags_flow(app_client, auth_headers, make_list):
    lid = make_list("HT List #1")
    # Add via query param API
    r = app_client.post(f"/lists/{lid}/hashtags", params={"tag": "#alpha"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    # Idempotent
    r2 = app_client.post(f"/lists/{lid}/hashtags", params={"tag": "#alpha"}, headers=auth_headers)
    assert r2.status_code == 200
    # Add via JSON API
    r3 = app_client.post(f"/lists/{lid}/hashtags/json", json={"tag": "beta"}, headers=auth_headers)
    assert r3.status_code == 200
    # Get tags (list-level only)
    r4 = app_client.get(f"/lists/{lid}/hashtags", headers=auth_headers)
    assert r4.status_code == 200
    tags = r4.json()["hashtags"]
    assert set(tags) >= {"#alpha", "#beta"}


def test_get_list_hashtags_with_todo_tags(app_client, auth_headers, make_list, make_todo):
    lid = make_list("HT List #2")
    # Create a todo with hashtags in text
    _tid = make_todo(lid, text="Do something #x #y")
    r = app_client.get(f"/lists/{lid}/hashtags", params={"include_todo_tags": "1"}, headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert "todo_hashtags" in data or "hashtags" in data


def test_remove_list_hashtag(app_client, auth_headers, make_list):
    lid = make_list("HT List #3")
    app_client.post(f"/lists/{lid}/hashtags", params={"tag": "#gone"}, headers=auth_headers)
    r = app_client.delete(f"/lists/{lid}/hashtags", params={"tag": "#gone"}, headers=auth_headers)
    assert r.status_code == 200
    # Removing again should 404 link not found
    r2 = app_client.delete(f"/lists/{lid}/hashtags", params={"tag": "#gone"}, headers=auth_headers)
    assert r2.status_code == 404


def test_hashtag_negative_cases(app_client, auth_headers, make_list):
    lid = make_list("HT List #4")
    # Missing tag in JSON
    r = app_client.post(f"/lists/{lid}/hashtags/json", json={}, headers=auth_headers)
    assert r.status_code == 400
    # Invalid tag normalization (e.g., empty)
    r2 = app_client.post(f"/lists/{lid}/hashtags", params={"tag": "   "}, headers=auth_headers)
    assert r2.status_code == 400
    # Remove non-existent hashtag
    r3 = app_client.delete(f"/lists/{lid}/hashtags", params={"tag": "#nope"}, headers=auth_headers)
    assert r3.status_code in (404, 400)
