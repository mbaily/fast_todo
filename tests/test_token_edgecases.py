import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import async_session, init_db
from app.models import User
from app.auth import pwd_context, create_access_token
from jose import JWTError, jwt
from datetime import timedelta
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
            q = await sess.exec(select(User).where(User.username == username))
            return q.first()
        await sess.refresh(u)
        return u


@pytest.mark.asyncio
async def test_expired_token_is_rejected(prepare_db):
    transport = ASGITransport(app=app)
    # create user
    await create_user("hank", "hankpass")
    # create an expired token for hank
    expired = create_access_token({"sub": "hank"}, expires_delta=timedelta(seconds=-10))

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers.update({"Authorization": f"Bearer {expired}"})
        r = await ac.post("/lists", params={"name": "should-fail"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_tampered_token_is_rejected(prepare_db):
    transport = ASGITransport(app=app)
    await create_user("ivy", "ivypass")
    valid = create_access_token({"sub": "ivy"})
    # tamper by appending an extra character to the token (guaranteed invalid)
    tampered = valid + "x"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers.update({"Authorization": f"Bearer {tampered}"})
        r = await ac.get("/lists")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_token_for_nonexistent_user_is_rejected(prepare_db):
    transport = ASGITransport(app=app)
    # create token for ghost user that does not exist
    ghost_token = create_access_token({"sub": "ghost_user_12345"})

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers.update({"Authorization": f"Bearer {ghost_token}"})
        r = await ac.get("/lists")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_token_payload_contains_sub_and_exp(prepare_db):
    transport = ASGITransport(app=app)
    await create_user("jack", "jackpass")
    # get token via auth endpoint
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/auth/token", json={"username": "jack", "password": "jackpass"})
        assert resp.status_code == 200
        token = resp.json().get("access_token")
        assert token
        # decode the token (we don't need to verify signature here, but do so to ensure structure)
        from app.auth import SECRET_KEY, ALGORITHM
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload.get("sub") == "jack"
        assert payload.get("exp") is not None
