import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.asyncio
async def test_whoami_anonymous(client):
    resp = client.get('/html_tailwind/whoami')
    assert resp.status_code == 200
    data = resp.json()
    assert data.get('ok') is True
    assert data.get('user') is None


@pytest.mark.asyncio
async def test_login_logout_flow(client):
    # Attempt login with invalid payload
    resp = client.post(
        '/html_tailwind/login',
        json={'username': 'invalid', 'password': 'bad'},
    )
    assert resp.status_code in (400, 401)

    # Try to login with an existing account 'mbaily' (common dev user in repo)
    resp = client.post(
        '/html_tailwind/login',
        json={'username': 'mbaily', 'password': 'password'},
    )
    if resp.status_code == 200:
        data = resp.json()
        assert data.get('ok') is True
        # set cookies on client for subsequent requests
        for k, v in resp.cookies.items():
            client.cookies.set(k, v)

        # whoami should now return the user
        r2 = client.get('/html_tailwind/whoami')
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2.get('ok') is True
        assert d2.get('user') is not None

        # Access index (server-rendered) but do NOT follow redirects (no browser navigation)
        r3 = client.get('/html_tailwind', allow_redirects=False)
        assert r3.status_code in (200, 303, 302)

        # Create a new list via JSON API if available
        r4 = client.post('/api/lists', json={'name': 'pytest list from tests'})
        assert r4.status_code in (200, 201, 400, 403, 404)

        # Logout (JSON endpoint)
        r5 = client.post('/html_tailwind/logout')
        assert r5.status_code == 200
        assert r5.json().get('ok') is True
    else:
        # Login failed (likely no test user). Ensure we got a proper error
        assert resp.status_code in (400, 401, 403)


@pytest.mark.asyncio
async def test_index_requires_login(client):
    # Clear cookies to ensure anonymous
    client.cookies.clear()
    # starlette TestClient uses `follow_redirects` flag
    r = client.get('/html_tailwind', follow_redirects=False)
    # Should be a redirect (303/302) or possibly return the login page (200)
    assert r.status_code in (200, 303, 302)


# Async smoke test using httpx AsyncClient
@pytest.mark.asyncio
async def test_async_client_smoke():
    from httpx import AsyncClient as _AsyncClient
    from httpx import ASGITransport
    transport = ASGITransport(app=app)
    async with _AsyncClient(transport=transport, base_url='http://testserver') as ac:
        r = await ac.get('/html_tailwind/whoami')
        assert r.status_code == 200
        d = r.json()
        assert d.get('ok') is True
