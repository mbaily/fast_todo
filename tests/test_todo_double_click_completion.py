import pytest
from sqlmodel import select
from app.db import async_session
from app.models import TodoCompletion, ListState
from app.auth import create_csrf_token


async def _is_todo_completed(todo_id: int) -> bool:
    async with async_session() as sess:
        q = await sess.exec(
            select(TodoCompletion)
            .where(TodoCompletion.todo_id == todo_id)
            .where(TodoCompletion.done == True)
        )
        return q.first() is not None


@pytest.mark.asyncio
async def test_todo_double_click_same_param_leaves_wrong_state(client, ensure_db):
    # Create a list and a todo
    r = await client.post('/lists', params={'name': 'double-click-list'})
    assert r.status_code in (200, 201)
    list_id = r.json()['id']

    r2 = await client.post('/todos', json={'text': 'double click todo', 'list_id': list_id})
    assert r2.status_code == 200
    todo_id = r2.json()['id']

    # Sanity: initially not completed
    assert (await _is_todo_completed(todo_id)) is False

    # Simulate two quick clicks on the rendered form. With the frontend fix the
    # hidden 'done' input is kept in sync, so the second submit should send the
    # opposite value.
    r3 = await client.post(
        f'/html_no_js/todos/{todo_id}/complete',
        data={'done': 'true'},
        follow_redirects=False,
    )
    assert r3.status_code in (302, 303)
    # First click marks it completed
    assert (await _is_todo_completed(todo_id)) is True

    # Second click should send done=false (toggle back)
    r4 = await client.post(
        f'/html_no_js/todos/{todo_id}/complete',
        data={'done': 'false'},
        follow_redirects=False,
    )
    assert r4.status_code in (302, 303)

    # Expected: two toggles should end up not completed (False)
    assert (await _is_todo_completed(todo_id)) is False


@pytest.mark.asyncio
async def test_list_double_click_via_two_requests_toggles_back(client, ensure_db):
    # Create a list
    r = await client.post('/lists', params={'name': 'list-double-click'})
    assert r.status_code in (200, 201)
    list_id = r.json()['id']

    # Helper to read list.completed
    async def get_list_completed(lid: int) -> bool:
        async with async_session() as sess:
            lst = await sess.get(ListState, lid)
            return bool(getattr(lst, 'completed', False)) if lst else False

    # CSRF token for HTML endpoint
    csrf = create_csrf_token('testuser')

    # Mark complete
    r2 = await client.post(
        f'/html_no_js/lists/{list_id}/complete',
        data={'completed': 'true', '_csrf': csrf},
        follow_redirects=False,
    )
    assert r2.status_code in (302, 303)
    assert await get_list_completed(list_id) is True

    # Mark incomplete (simulating the second click after a page reload)
    r3 = await client.post(
        f'/html_no_js/lists/{list_id}/complete',
        data={'completed': 'false', '_csrf': csrf},
        follow_redirects=False,
    )
    assert r3.status_code in (302, 303)
    assert await get_list_completed(list_id) is False
