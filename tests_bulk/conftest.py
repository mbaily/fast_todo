import os
import json
import sys
import random
import string
import tempfile
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient


def _rand_str(prefix: str = "u", n: int = 8) -> str:
    return prefix + "_" + "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


@pytest.fixture(scope="session")
def temp_db_path(tmp_path_factory):
    # Create an isolated sqlite file for this test session
    p = tmp_path_factory.mktemp("db") / "bulk_fast_todo.db"
    return str(p)


@pytest.fixture(scope="session")
def test_settings(temp_db_path):
    # Minimal env so app starts inside TestClient
    os.environ["SECRET_KEY"] = "test-secret-key-for-bulk-suite"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{temp_db_path}"
    # CSRF expiry in seconds
    os.environ["CSRF_TOKEN_EXPIRE_SECONDS"] = "1800"
    # Disable optional extras that may reach out of process
    os.environ.pop("ENABLE_DEBUGPY", None)
    os.environ.pop("SSH_REPL_ENABLE", None)
    os.environ.pop("ENABLE_DB_TRACING", None)
    return {}


@pytest.fixture(scope="session")
def app_client(test_settings):
    # Import after env is set so app picks up DATABASE_URL/SECRET_KEY
    # Ensure workspace root is on sys.path
    root = os.path.abspath(os.getcwd())
    if root not in sys.path:
        sys.path.insert(0, root)
    from app.main import app
    # Use context manager so FastAPI lifespan runs (init_db creates tables)
    with TestClient(app) as client:
        yield client


def _create_user_and_login(client: TestClient, username: str | None = None, password: str | None = None):
    username = username or _rand_str("user")
    password = password or "pw_" + _rand_str(n=6)
    # Create user directly via DB helper script API surface:
    # There is no public signup endpoint; use the low-level DB to insert.
    from app.db import async_session
    from app.models import User
    from passlib.context import CryptContext
    import anyio

    pwd = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")

    async def _insert_user():
        async with async_session() as s:
            u = User(username=username, password_hash=pwd.hash(password))
            s.add(u)
            await s.commit()

    anyio.run(_insert_user)

    # Login via token endpoint to get bearer token
    r = client.post("/auth/token", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    tok = r.json()["access_token"]
    return username, password, tok


@pytest.fixture(scope="session")
def auth_token(app_client: TestClient):
    _, _, tok = _create_user_and_login(app_client)
    return tok


@pytest.fixture(scope="session")
def auth_headers(auth_token: str):
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture(scope="session")
def default_list_id(app_client: TestClient, auth_headers: dict):
    # Create one base list and set as server default for tests that need it
    name = "BulkRootList"
    r = app_client.post("/lists", params={"name": name}, headers=auth_headers)
    assert r.status_code == 200, r.text
    list_id = r.json()["id"]
    # Set as default list for coverage of that path
    r2 = app_client.post(f"/server/default_list/{list_id}", headers=auth_headers)
    assert r2.status_code == 200, r2.text
    return list_id


@pytest.fixture(scope="session")
def make_list(app_client: TestClient, auth_headers: dict):
    def _make(name: str):
        r = app_client.post("/lists", params={"name": name}, headers=auth_headers)
        assert r.status_code == 200, r.text
        return r.json()["id"]
    return _make


@pytest.fixture(scope="session")
def make_todo(app_client: TestClient, auth_headers: dict):
    def _make(list_id: int, text: str, note: str | None = None, priority: int | None = None):
        payload = {"text": text, "list_id": list_id}
        if note is not None:
            payload["note"] = note
        if priority is not None:
            payload["priority"] = priority
        r = app_client.post("/todos", json=payload, headers=auth_headers)
        assert r.status_code == 200, r.text
        return r.json()["id"]
    return _make
