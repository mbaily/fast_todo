import os
import sys
import random
import string
from fastapi.testclient import TestClient
import pytest


def _rand(n=8):
    import secrets
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(n))


@pytest.fixture(scope="session")
def e2e_env(tmp_path_factory):
    dbfile = tmp_path_factory.mktemp("e2e_db") / "e2e_fast_todo.db"
    os.environ["SECRET_KEY"] = "e2e-secret-key"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{dbfile}"
    os.environ["CSRF_TOKEN_EXPIRE_SECONDS"] = "3600"
    os.environ.pop("ENABLE_DEBUGPY", None)
    # Allow log endpoints for SSE tests
    os.environ["ENABLE_LOG_ENDPOINT"] = "1"
    return {}


@pytest.fixture(scope="session")
def e2e_client(e2e_env):
    root = os.path.abspath(os.getcwd())
    if root not in sys.path:
        sys.path.insert(0, root)
    from app.main import app
    with TestClient(app) as c:
        yield c


def _create_user(client: TestClient, username: str, password: str):
    from app.db import async_session
    from app.models import User
    from passlib.context import CryptContext
    import anyio
    pwd = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")
    async def _insert():
        async with async_session() as s:
            s.add(User(username=username, password_hash=pwd.hash(password)))
            await s.commit()
    anyio.run(_insert)


@pytest.fixture(scope="session")
def user_a(e2e_client):
    u, p = "user_" + _rand(), "pw_" + _rand(6)
    _create_user(e2e_client, u, p)
    return u, p


@pytest.fixture(scope="session")
def user_b(e2e_client):
    u, p = "user_" + _rand(), "pw_" + _rand(6)
    _create_user(e2e_client, u, p)
    return u, p


@pytest.fixture(scope="session")
def bearer_headers_a(e2e_client, user_a):
    u, p = user_a
    r = e2e_client.post("/auth/token", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    tok = r.json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="session")
def bearer_headers_b(e2e_client, user_b):
    u, p = user_b
    r = e2e_client.post("/auth/token", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    tok = r.json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="session")
def login_cookie_csrf(e2e_client, user_a):
    # Tailwind JSON login issues cookies incl. csrf_token
    u, p = user_a
    r = e2e_client.post("/html_tailwind/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    csrf = e2e_client.cookies.get("csrf_token")
    assert csrf, "csrf cookie missing after login"
    return csrf
