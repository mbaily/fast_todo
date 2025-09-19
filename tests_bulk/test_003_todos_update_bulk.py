import random
import string

import pytest


pytestmark = pytest.mark.bulk


def _text(i: int) -> str:
    s = f"UpdateSeed {i} " + "".join(random.choice(string.ascii_letters) for _ in range(8))
    if i % 9 == 0:
        s += " #alpha"
    return s


@pytest.mark.parametrize("i", list(range(250)))
def test_update_todo_roundtrip(app_client, auth_headers, default_list_id, i):
    # Create
    r = app_client.post("/todos", json={"text": _text(i), "list_id": default_list_id}, headers=auth_headers)
    assert r.status_code == 200, r.text
    todo = r.json()
    tid = todo["id"]
    # Update
    new_text = f"Updated {i}"
    new_note = "N:" + "".join(random.choice(string.ascii_letters) for _ in range(12))
    new_pri = (i % 10) + 1
    r2 = app_client.patch(f"/todos/{tid}", json={"text": new_text, "note": new_note, "priority": new_pri}, headers=auth_headers)
    assert r2.status_code == 200, r2.text
    updated = r2.json()
    assert updated["id"] == tid
    assert updated["text"].startswith("Updated")
    assert updated.get("note", "").startswith("N:")
    assert updated.get("priority") == new_pri
