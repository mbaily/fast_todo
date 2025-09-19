import pytest


pytestmark = pytest.mark.bulk


PHRASES = [
    "every monday at 9am",
    "on the 15th of each month",
    "every day",
    "first friday of every month",
    "every 2 weeks starting tomorrow",
]


@pytest.mark.parametrize("i", list(range(250)))
def test_parse_text_to_rrule_endpoint(app_client, auth_headers, i):
    phrase = PHRASES[i % len(PHRASES)]
    r = app_client.post("/parse_text_to_rrule", params={"text": phrase}, headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "rrule" in data
    # dtstart may be null but key should exist
    assert "dtstart" in data


def test_runtime_flags_accessible(app_client):
    r = app_client.get("/server/runtime_flags")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
