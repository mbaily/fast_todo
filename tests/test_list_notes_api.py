import pytest


@pytest.mark.asyncio
async def test_list_notes_crud(client):
    # obtain a fresh token for autotest user and use JSON create list endpoint explicitly
    tok_resp = await client.post('/auth/token', json={'username': '__autotest__', 'password': 'p'})
    assert tok_resp.status_code == 200, tok_resp.text
    token = tok_resp.json().get('access_token')
    assert token
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    r = await client.post('/client/json/lists', json={'name': 'NotesTest'}, headers=headers)
    assert r.status_code == 200, r.text
    list_id = r.json().get('id') or r.json().get('list', {}).get('id')
    assert list_id
    r = await client.post(f'/client/json/lists/{list_id}/notes', json={'content': 'First note'}, headers=headers)
    assert r.status_code == 200, r.text
    note = r.json()['note']
    note_id = note['id']
    assert note['content'] == 'First note'
    r = await client.get(f'/client/json/lists/{list_id}/notes', headers=headers)
    assert r.status_code == 200, r.text
    notes = r.json()['notes']
    assert any(n['id'] == note_id for n in notes)
    r = await client.patch(f'/client/json/list_notes/{note_id}', json={'content': 'Updated'}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()['note']['content'] == 'Updated'
    r = await client.delete(f'/client/json/list_notes/{note_id}', headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()['deleted'] is True
    r = await client.get(f'/client/json/lists/{list_id}/notes', headers=headers)
    assert r.status_code == 200, r.text
    notes = r.json()['notes']
    assert all(n['id'] != note_id for n in notes)
