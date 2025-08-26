import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import init_db, async_session
from app.models import ListState, Todo, User
from sqlmodel import select


@pytest.mark.asyncio
async def test_autosave_endpoint(client: AsyncClient):
    # create a list and todo
    resp = await client.post('/lists', params={'name': 'autosavelist'})
    assert resp.status_code == 200
    lst = resp.json()
    # create a todo in list
    # the /todos endpoint expects query parameters for this API
    resp = await client.post('/todos', params={'text': 'test todo', 'list_id': lst['id']})
    assert resp.status_code == 200
    todo = resp.json()
    todo_id = todo['id']

    # get csrf token by hitting the login endpoint (we have token auth header already)
    # create csrf token by calling login page simulation
    # our login endpoint sets csrf_token cookie; but since client is authenticated via bearer, emulate cookie
    from app.auth import create_csrf_token
    csrf = create_csrf_token('testuser')
    # call the autosave (AJAX) POST with Accept: application/json and csrf cookie
    headers = {'Accept': 'application/json'}
    cookies = {'csrf_token': csrf}
    resp = await client.post(f'/html_no_js/todos/{todo_id}/edit', data={'text': 'edited', 'note': 'autosaved note', '_csrf': csrf}, headers=headers, cookies=cookies)
    assert resp.status_code == 200
    data = resp.json()
    assert data['text'] == 'edited'
    assert data['note'] == 'autosaved note'
