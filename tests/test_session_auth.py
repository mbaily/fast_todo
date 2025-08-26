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
            # If the user already exists from previous runs, update their
            # password_hash so tests remain idempotent across runs.
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


@pytest.mark.asyncio
async def test_session_login_create_and_logout(prepare_db):
    transport = ASGITransport(app=app)
    # create user and login via HTML form to get session cookie
    await create_user("sessuser", "pw123")
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/html_no_js/login", data={"username": "sessuser", "password": "pw123"}, follow_redirects=True)
        # cookie should be set in the client's cookie jar after following the redirect
        assert client.cookies.get('session_token') is not None

        # create a list while authenticated (session cookie via client)
        rl = await client.post("/lists", params={"name": "session-list"})
        assert rl.status_code == 200
        lst = rl.json()
        lid = lst['id']

        # Now logout via html endpoint; this should remove cookies and server session
        rlog = await client.post('/html_no_js/logout')
        # client cookie jar should no longer have session_token
        assert client.cookies.get('session_token') is None

        # anonymous delete attempt should be forbidden because list has an owner
        rdel = await client.delete(f"/lists/{lid}")
        assert rdel.status_code == 403


@pytest.mark.asyncio
async def test_other_user_cannot_delete(prepare_db):
    transport = ASGITransport(app=app)
    # create two users
    await create_user('alice', 'a1')
    await create_user('bob', 'b1')

    # alice logs in and creates a list
    async with AsyncClient(transport=transport, base_url="http://test") as aclient:
        ra = await aclient.post('/html_no_js/login', data={'username': 'alice', 'password': 'a1'}, follow_redirects=True)
        # rely on server behavior (creating a list) rather than brittle client cookie inspection
        r = await aclient.post('/lists', params={'name': 'alice-list'})
        assert r.status_code == 200
        lid = r.json()['id']

    # bob logs in and attempts to delete alice's list
    async with AsyncClient(transport=transport, base_url="http://test") as bclient:
        rb = await bclient.post('/html_no_js/login', data={'username': 'bob', 'password': 'b1'}, follow_redirects=True)
        rdel = await bclient.delete(f"/lists/{lid}")
        # should be forbidden
        assert rdel.status_code == 403
