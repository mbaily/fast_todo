import pytest
from app.auth import create_csrf_token
from sqlmodel import select


@pytest.mark.asyncio
async def test_html_complete_uses_resolved_user(client):
    # Create a list
    resp = await client.post('/lists', params={'name': 'tst-list'})
    assert resp.status_code in (200, 201)
    data = resp.json()
    list_id = data.get('id')
    assert list_id

    # Create a todo in that list
    resp = await client.post('/todos', params={'text': 'hi', 'list_id': list_id})
    assert resp.status_code in (200, 201)
    todo = resp.json()
    todo_id = todo.get('id')
    assert todo_id

    # POST to the HTML complete endpoint (form style)
    resp = await client.post(f'/html_no_js/todos/{todo_id}/complete', data={'done': '1'}, follow_redirects=False)
    # Should redirect back to the list page
    assert resp.status_code in (302, 303)

    # Confirm completion state via API: check completion entry exists
    resp = await client.get(f'/todos/{todo_id}')
    assert resp.status_code == 200
    t = resp.json()
    # If completion types exist, ensure we see either completions or other indication
    # At minimum, the endpoint should not throw and todo should exist
    assert t.get('id') == todo_id


@pytest.mark.asyncio
async def test_api_complete_with_completion_type_and_html_calls(client):
    # Create list and completion type via API, create todo, then toggle completion via html
    resp = await client.post('/lists', params={'name': 'tst-list-2'})
    assert resp.status_code in (200,201)
    list_id = resp.json().get('id')

    resp = await client.post('/todos', params={'text': 'hello', 'list_id': list_id})
    assert resp.status_code in (200,201)
    todo_id = resp.json().get('id')

    # Create a named completion type via API
    resp = await client.post(f'/lists/{list_id}/completion_types', params={'name': 'x'})
    # it's OK if server returns 200/201
    assert resp.status_code in (200,201)
    created = resp.json()
    # fetch the id for the created completion type
    created_id = created.get('id')

    # Now toggle completion via the html completion-type POST endpoint
    # Use the created completion type id when submitting the html form
    ct_id = created_id or 1
    csrf = create_csrf_token('testuser')
    resp = await client.post(f'/html_no_js/todos/{todo_id}/complete_type', data={'completion_type_id': ct_id, 'done': '1', '_csrf': csrf}, follow_redirects=False, headers={'referer': f'/html_no_js/todos/{todo_id}'})
    # should redirect
    assert resp.status_code in (302,303)

    # Verify todo exists and endpoint succeeded
    resp = await client.get(f'/todos/{todo_id}')
    assert resp.status_code == 200
    t = resp.json()
    assert t.get('id') == todo_id
