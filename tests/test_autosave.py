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
    resp = await client.post('/todos', json={'text': 'test todo', 'list_id': lst['id']})
    assert resp.status_code == 200
    todo = resp.json()
    todo_id = todo['id']

    # Use the JSON PATCH API to autosave edits (server expects JSON)
    resp = await client.patch(f'/todos/{todo_id}', json={'text': 'edited', 'note': 'autosaved note'})
    assert resp.status_code == 200
    data = resp.json()
    assert data['text'] == 'edited'
    assert data['note'] == 'autosaved note'
