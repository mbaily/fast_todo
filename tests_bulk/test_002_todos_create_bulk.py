import random
import string

import pytest


pytestmark = pytest.mark.bulk


def _rand_text(i: int) -> str:
    base = f"ParamEvent {i} - " + "".join(random.choice(string.ascii_letters) for _ in range(10))
    # sprinkle simple dates to hit calendar parsing paths
    if i % 10 == 0:
        base += " 2025-09-19"
    if i % 13 == 0:
        base += " next monday"
    # include some hashtags
    if i % 6 == 0:
        base += " #tag" + str(i % 3)
    return base


@pytest.mark.parametrize("i", list(range(250)))
def test_create_todo_in_default_list(app_client, auth_headers, default_list_id, i):
    text = _rand_text(i)
    note = None
    if i % 4 == 0:
        note = "Note for todo " + str(i)
    payload = {"text": text, "list_id": default_list_id}
    if note is not None:
        payload["note"] = note
    if i % 5 == 0:
        payload["priority"] = (i % 10) + 1
    r = app_client.post("/todos", json=payload, headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["id"] > 0
    assert data["list_id"] == default_list_id
    assert isinstance(data.get("created_at"), str)
