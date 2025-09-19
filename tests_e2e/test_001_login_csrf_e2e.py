import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("i", list(range(200)))
def test_login_and_csrf_cookie(e2e_client, user_a, i):
    # Fresh login to assert cookies present; reuse same creds
    u, p = user_a
    r = e2e_client.post("/html_tailwind/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    csrf = e2e_client.cookies.get("csrf_token")
    session = e2e_client.cookies.get("session_token")
    assert csrf and session
