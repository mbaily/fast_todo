import json
import uuid
import pytest


@pytest.mark.asyncio
async def test_service_worker_and_manifest_sync_endpoints(client):
    # service worker
    resp = await client.get('/service-worker.js')
    assert resp.status_code == 200
    assert 'application/javascript' in resp.headers.get('content-type', '')
    assert resp.headers.get('Cache-Control') == 'no-cache'

    # manifest
    resp = await client.get('/manifest.json')
    assert resp.status_code == 200
    assert 'application/manifest+json' in resp.headers.get('content-type', '')
    assert resp.headers.get('Cache-Control') == 'no-cache'


@pytest.mark.asyncio
async def test_sync_get_and_post(client):
    # GET sync with no changes should return empty lists/todos and a server_ts
    resp = await client.get('/sync')
    assert resp.status_code == 200
    data = resp.json()
    assert 'server_ts' in data
    assert isinstance(data.get('lists'), list)
    assert isinstance(data.get('todos'), list)

    # POST sync create_list and create_todo with client_ids
    ops = {
        'ops': [
            {'op': 'create_list', 'payload': {'name': 'PWA Test', 'client_id': 'c_l_1'}},
        ]
    }
    resp = await client.post('/sync', json=ops)
    assert resp.status_code == 200
    results = resp.json().get('results')
    assert results and results[0]['status'] == 'ok'
    assert results[0].get('client_id') == 'c_l_1'
    list_id = results[0]['id']

    # create a todo in that list
    ops = {'ops': [{'op': 'create_todo', 'payload': {'text': 'pwa todo', 'note': 'note', 'list_id': list_id, 'client_id': 'c_t_1'}}]}
    resp = await client.post('/sync', json=ops)
    assert resp.status_code == 200
    r = resp.json().get('results')[0]
    assert r['status'] == 'ok'
    assert r.get('client_id') == 'c_t_1'

    # Now GET /sync since server_ts should return the created list/todo when using old since
    server_ts = (await client.get('/sync')).json()['server_ts']
    # query with a since in the past to ensure we pick up changes
    resp = await client.get('/sync', params={'since': '2000-01-01T00:00:00+00:00'})
    data = resp.json()
    assert any(l['name'] == 'PWA Test' for l in data['lists'])
    assert any(t['text'] == 'pwa todo' for t in data['todos'])


@pytest.mark.asyncio
async def test_sync_create_todo_idempotency(client):
    # create a list first
    resp = await client.post('/sync', json={'ops': [{'op': 'create_list', 'payload': {'name': 'Idempotency List'}}]})
    assert resp.status_code == 200
    list_id = resp.json()['results'][0]['id']

    op_id = 'op-create-todo-1'
    ops = {'ops': [{'op': 'create_todo', 'payload': {'text': 'idem todo', 'note': 'note', 'list_id': list_id, 'client_id': 'c1', 'op_id': op_id}}]}
    r1 = await client.post('/sync', json=ops)
    assert r1.status_code == 200
    res1 = r1.json()['results'][0]
    assert res1['status'] == 'ok'
    first_id = res1['id']

    # retry same op_id -- should return same result and not create a second todo
    r2 = await client.post('/sync', json=ops)
    assert r2.status_code == 200
    res2 = r2.json()['results'][0]
    assert res2['status'] == 'ok'
    assert res2.get('id') == first_id

    # Ensure only one todo with that text exists via sync GET
    data = (await client.get('/sync', params={'since': '2000-01-01T00:00:00+00:00'})).json()
    matches = [t for t in data['todos'] if t['text'] == 'idem todo']
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_sync_update_todo_idempotency(client):
    # create a list and a todo
    resp = await client.post('/sync', json={'ops': [{'op': 'create_list', 'payload': {'name': 'Update Idem List'}}]})
    list_id = resp.json()['results'][0]['id']
    resp = await client.post('/sync', json={'ops': [{'op': 'create_todo', 'payload': {'text': 'to update', 'note': '', 'list_id': list_id}}]})
    todo_id = resp.json()['results'][0]['id']

    op_id = f'op-update-{uuid.uuid4()}'
    ops = {'ops': [{'op': 'update_todo', 'payload': {'id': todo_id, 'text': 'updated text', 'op_id': op_id}}]}
    r1 = await client.post('/sync', json=ops)
    assert r1.status_code == 200
    res1 = r1.json()['results'][0]
    assert res1['status'] == 'ok'
    assert res1['id'] == todo_id

    # retry same op_id
    r2 = await client.post('/sync', json=ops)
    assert r2.status_code == 200
    res2 = r2.json()['results'][0]
    assert res2['status'] == 'ok'
    assert res2['id'] == todo_id

    # ensure the todo was updated and only one exists
    data = (await client.get('/sync', params={'since': '2000-01-01T00:00:00+00:00'})).json()
    matches = [t for t in data['todos'] if t['id'] == todo_id]
    assert len(matches) == 1
    assert matches[0]['text'] == 'updated text'


@pytest.mark.asyncio
async def test_sync_delete_todo_idempotency(client):
    # create a list and a todo
    resp = await client.post('/sync', json={'ops': [{'op': 'create_list', 'payload': {'name': 'Delete Idem List'}}]})
    list_id = resp.json()['results'][0]['id']
    resp = await client.post('/sync', json={'ops': [{'op': 'create_todo', 'payload': {'text': 'to delete', 'note': '', 'list_id': list_id}}]})
    todo_id = resp.json()['results'][0]['id']

    op_id = f'op-delete-{uuid.uuid4()}'
    ops = {'ops': [{'op': 'delete_todo', 'payload': {'id': todo_id, 'op_id': op_id}}]}
    r1 = await client.post('/sync', json=ops)
    assert r1.status_code == 200
    res1 = r1.json()['results'][0]
    assert res1['status'] == 'ok'

    # retry same op_id
    r2 = await client.post('/sync', json=ops)
    assert r2.status_code == 200
    res2 = r2.json()['results'][0]
    assert res2['status'] == 'ok'

    # ensure the todo is gone
    data = (await client.get('/sync', params={'since': '2000-01-01T00:00:00+00:00'})).json()
    matches = [t for t in data['todos'] if t['id'] == todo_id]
    assert len(matches) == 0
