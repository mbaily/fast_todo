import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import async_session, init_db
from app.models import User, ListState
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
async def test_patch_list_and_hashtags_json(prepare_db):
    transport = ASGITransport(app=app)
    # create test user
    await create_user('itestuser', 'pw')
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        # login via html_no_js flow to get session cookie
        r = await client.post('/html_no_js/login', data={'username': 'itestuser', 'password': 'pw'}, follow_redirects=True)
        assert r.status_code in (200, 302, 303)

        # create a list
        rl = await client.post('/lists', params={'name': 'itest list'})
        assert rl.status_code == 200
        lst = rl.json()
        lid = lst['id']

        # PATCH: change name
        rp = await client.patch(f'/lists/{lid}', json={'name': 'updated name'})
        assert rp.status_code == 200
        assert rp.json().get('name') == 'updated name'

        # PATCH: set priority to 5
        rp2 = await client.patch(f'/lists/{lid}', json={'priority': 5})
        assert rp2.status_code == 200
        assert rp2.json().get('priority') == 5

        # Add a tag via JSON endpoint
        radd = await client.post(f'/lists/{lid}/hashtags/json', json={'tag': 'tag1'})
        assert radd.status_code == 200
        assert radd.json().get('tag') in ('#tag1', 'tag1',)

        # Get combined tags
        rg = await client.get(f'/lists/{lid}/hashtags?combine=true')
        assert rg.status_code == 200
        data = rg.json()
        assert 'hashtags' in data and any('tag1' in t for t in data['hashtags'])

        # Remove tag via JSON endpoint
        rrem = await client.request('DELETE', f'/lists/{lid}/hashtags/json', json={'tag': 'tag1'})
        assert rrem.status_code == 200
        assert rrem.json().get('removed') in ('#tag1', 'tag1')
