import pytest
import pytest_asyncio
import asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import init_db
from app.models import User
from app.auth import pwd_context, create_access_token, SECRET_KEY, ALGORITHM
from jose import jwt
from datetime import timedelta
from sqlmodel import select
from app.db import async_session


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
async def test_short_lived_token_expires(prepare_db):
    transport = ASGITransport(app=app)
    await create_user("short", "shortpass")
    token = create_access_token({"sub": "short"}, expires_delta=timedelta(seconds=1))

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers.update({"Authorization": f"Bearer {token}"})
        r = await ac.get("/lists")
        # token should work immediately
        assert r.status_code == 200
        # wait for expiry
        await asyncio.sleep(2)
        r2 = await ac.get("/lists")
        assert r2.status_code == 401


@pytest.mark.asyncio
async def test_missing_sub_claim_is_rejected(prepare_db):
    transport = ASGITransport(app=app)
    await create_user("nosub", "pass")
    token = create_access_token({"foo": "bar"})

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers.update({"Authorization": f"Bearer {token}"})
        r = await ac.get("/lists")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_secret_rotation_invalidates_existing_tokens(prepare_db):
    transport = ASGITransport(app=app)
    await create_user("rot", "rotpass")
    token = create_access_token({"sub": "rot"}, expires_delta=timedelta(minutes=5))

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers.update({"Authorization": f"Bearer {token}"})
        r = await ac.get("/lists")
        assert r.status_code == 200
        # rotate secret
        from app import auth as authmod
        old = authmod.SECRET_KEY
        authmod.SECRET_KEY = "rotated_secret_for_test"
        r2 = await ac.get("/lists")
        assert r2.status_code == 401
        # restore
        authmod.SECRET_KEY = old


@pytest.mark.asyncio
async def test_algorithm_mismatch_token_is_rejected(prepare_db):
    transport = ASGITransport(app=app)
    await create_user("alg", "algpass")
    # create token with different alg
    token = jwt.encode({"sub": "alg"}, SECRET_KEY, algorithm="HS384")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers.update({"Authorization": f"Bearer {token}"})
        r = await ac.get("/lists")
        assert r.status_code == 401
