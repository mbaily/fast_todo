import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import async_session, init_db
from app.models import User
from app.auth import pwd_context
from sqlmodel import select


@pytest_asyncio.fixture
async def prepare_db():
    await init_db()
    yield


async def create_user(username: str, password: str):
    async with async_session() as sess:
        ph = pwd_context.hash(password)
        u = User(username=username, password_hash=ph, is_admin=False)
        sess.add(u)
        try:
            await sess.commit()
        except Exception:
            await sess.rollback()
            # if a user with this username already exists (from previous runs),
            # update its password_hash to the current hash so tests authenticate
            q = await sess.exec(select(User).where(User.username == username))
            existing = q.first()
            if existing:
                existing.password_hash = ph
                sess.add(existing)
                try:
                    await sess.commit()
                except Exception:
                    await sess.rollback()
                await sess.refresh(existing)
                return existing
            return None
        await sess.refresh(u)
        return u


async def get_token(client: AsyncClient, username: str, password: str):
    r = await client.post("/auth/token", json={"username": username, "password": password})
    return r


@pytest.mark.asyncio
async def test_token_endpoint_and_invalid_credentials(prepare_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # create user
        await create_user("alice", "alicepass")

        # valid credentials
        r = await get_token(ac, "alice", "alicepass")
        assert r.status_code == 200
        assert r.json().get("access_token")

        # invalid credentials
        r2 = await get_token(ac, "alice", "wrongpass")
        assert r2.status_code == 401


@pytest.mark.asyncio
async def test_per_user_list_isolation_and_todo_forbidden(prepare_db):
    transport = ASGITransport(app=app)

    # create users
    await create_user("alice", "alicepass")
    await create_user("bob", "bobpass")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # get tokens for both
        ra = await get_token(ac, "alice", "alicepass")
        ta = ra.json().get("access_token")
        rb = await get_token(ac, "bob", "bobpass")
        tb = rb.json().get("access_token")

    # alice client
    async with AsyncClient(transport=transport, base_url="http://test") as acli:
        acli.headers.update({"Authorization": f"Bearer {ta}"})
        r = await acli.post("/lists", params={"name": "alice-list"})
        assert r.status_code == 200
        alist = r.json()
        assert alist.get("name") == "alice-list"

        # create a todo in alice's list
        rtodo = await acli.post("/todos", json={"text": "alice task", "list_id": alist["id"]})
        assert rtodo.status_code == 200
        todo = rtodo.json()

    # bob client should not see alice's list
    async with AsyncClient(transport=transport, base_url="http://test") as bcli:
        bcli.headers.update({"Authorization": f"Bearer {tb}"})
        rlists = await bcli.get("/lists")
        blists = rlists.json()
        assert all(l.get("name") != "alice-list" for l in blists)

        # bob cannot create a todo in alice's list (forbidden)
        r = await bcli.post("/todos", json={"text": "bad", "list_id": alist["id"]})
        assert r.status_code == 403

        # bob cannot GET alice's todo
        rget = await bcli.get(f"/todos/{todo['id']}")
        assert rget.status_code == 403
