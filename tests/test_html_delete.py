import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.auth import create_csrf_token


@pytest.mark.asyncio
async def test_html_delete_does_not_500(client: AsyncClient):
    # create a list
    resp = await client.post('/lists', params={'name': 'delete-list'})
    assert resp.status_code == 200
    lst = resp.json()
    # create a todo
    resp = await client.post('/todos', params={'text': 'to-delete', 'list_id': lst['id']})
    assert resp.status_code == 200
    todo = resp.json()

    # obtain bearer token and set cookie-based auth + csrf (client already has Authorization header)
    token_resp = await client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
    assert token_resp.status_code == 200
    token = token_resp.json().get('access_token')
    assert token
    # set access token cookie and csrf cookie
    client.cookies.set('access_token', token)
    csrf = create_csrf_token('testuser')
    client.cookies.set('csrf_token', csrf)

    # call HTML delete endpoint (this used to cause a server error in some code paths)
    resp = await client.post(f'/html_no_js/todos/{todo["id"]}/delete', data={'_csrf': csrf}, follow_redirects=False)
    # should not be a server error
    assert resp.status_code in (303, 302, 200)
