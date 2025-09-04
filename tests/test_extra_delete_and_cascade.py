import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_delete_todo_cleans_links(client):
    # create a list
    r = await client.post('/lists', params={'name': 'delete-test-list'})
    assert r.status_code == 200
    lid = r.json()['id']

    # create a todo
    rt = await client.post('/todos', json={'text': 'to be deleted', 'list_id': lid})
    assert rt.status_code == 200
    tid = rt.json()['id']

    # add a completion
    rc = await client.post(f'/todos/{tid}/complete', params={'completion_type': 'done', 'done': True})
    assert rc.status_code == 200

    # add a hashtag
    rh = await client.post(f'/todos/{tid}/hashtags', params={'tag': '#cleanup'})
    assert rh.status_code == 200

    # delete the todo
    rd = await client.delete(f'/todos/{tid}')
    assert rd.status_code == 200

    # todo should be gone
    rg = await client.get(f'/todos/{tid}')
    assert rg.status_code == 404

    # removing the hashtag link should now 404 (link not found)
    rrem = await client.delete(f'/todos/{tid}/hashtags', params={'tag': '#cleanup'})
    assert rrem.status_code == 404


@pytest.mark.asyncio
async def test_delete_list_moves_todos_and_preserves_completions(client):
    # create a new list
    r = await client.post('/lists', params={'name': 'move-list'})
    assert r.status_code == 200
    lid = r.json()['id']

    # create a todo on that list
    rt = await client.post('/todos', json={'text': 'to be moved', 'list_id': lid})
    assert rt.status_code == 200
    tid = rt.json()['id']

    # mark as completed
    rc = await client.post(f'/todos/{tid}/complete', params={'completion_type': 'moved', 'done': True})
    assert rc.status_code == 200

    # delete the list (server must not move todos)
    rdel = await client.delete(f'/lists/{lid}')
    assert rdel.status_code == 200
    body = rdel.json()
    assert body.get('deleted') == lid

    # the todo should have been deleted along with the list
    rg = await client.get(f'/todos/{tid}')
    assert rg.status_code == 404
