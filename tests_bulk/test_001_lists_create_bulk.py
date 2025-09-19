import itertools
import random
import string

import pytest


pytestmark = pytest.mark.bulk


def _name(i: int) -> str:
    # include hashtags sometimes to exercise extraction & metadata
    base = f"List {i} " + "".join(random.choice(string.ascii_letters) for _ in range(6))
    if i % 5 == 0:
        base += " #work"
    if i % 7 == 0:
        base += " #home"
    return base


@pytest.mark.parametrize("i", list(range(250)))
def test_create_list_variants(app_client, auth_headers, i):
    name = _name(i)
    r = app_client.post("/lists", params={"name": name}, headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["id"] > 0
    assert isinstance(data["name"], str)
