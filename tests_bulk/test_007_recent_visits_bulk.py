import os
import pytest

pytestmark = pytest.mark.bulk


def test_record_list_and_todo_visits(app_client, auth_headers, make_list, make_todo, monkeypatch):
    # Keep top-N small to exercise shifting
    monkeypatch.setenv("RECENT_LISTS_TOP_N", "3")
    monkeypatch.setenv("RECENT_TODOS_TOP_N", "3")
    lids = [make_list(f"RV List {i}") for i in range(4)]
    # Record visits in order 0,1,2 then revisit 0 (should remain top)
    for lid in lids[:3]:
        r = app_client.post(f"/lists/{lid}/visit", headers=auth_headers)
        assert r.status_code == 200
    r = app_client.post(f"/lists/{lids[0]}/visit", headers=auth_headers)
    assert r.status_code == 200
    # Create a todo and record visit
    tid = make_todo(lids[0], text="recent visit todo")
    r2 = app_client.post(f"/todos/{tid}/visit", headers=auth_headers)
    assert r2.status_code == 200
