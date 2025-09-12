import pytest

pytestmark = pytest.mark.asyncio


async def test_get_list_hashtags_default_returns_list_tags(ensure_db, client):
    # create a list and add list-level hashtags
    r = await client.post('/lists', params={'name': 'lh-list'})
    assert r.status_code == 200
    lst = r.json()
    await client.post(f"/lists/{lst['id']}/hashtags", params={'tag': 'one'})
    await client.post(f"/lists/{lst['id']}/hashtags", params={'tag': 'two'})

    g = await client.get(f"/lists/{lst['id']}/hashtags")
    assert g.status_code == 200
    body = g.json()
    assert body['list_id'] == lst['id']
    assert 'hashtags' in body
    assert set(body['hashtags']) == set(['#one', '#two'])


async def test_get_list_hashtags_include_todo_tags_returns_separate_keys(ensure_db, client):
    # create list, create todos, add tags to both
    r = await client.post('/lists', params={'name': 'lh-list-2'})
    lst = r.json()
    t1 = (await client.post('/todos', json={'text': 't1', 'list_id': lst['id']})).json()
    t2 = (await client.post('/todos', json={'text': 't2', 'list_id': lst['id']})).json()
    await client.post(f"/lists/{lst['id']}/hashtags", params={'tag': 'L'})
    await client.post(f"/todos/{t1['id']}/hashtags", params={'tag': 'A'})
    await client.post(f"/todos/{t2['id']}/hashtags", params={'tag': 'B'})

    g = await client.get(
        f"/lists/{lst['id']}/hashtags",
        params={'include_todo_tags': '1'},
    )
    assert g.status_code == 200
    body = g.json()
    assert body['list_id'] == lst['id']
    assert 'list_hashtags' in body and 'todo_hashtags' in body
    assert set(body['list_hashtags']) == set(['#l'])
    assert set(body['todo_hashtags']) == set(['#a', '#b'])


async def test_get_list_hashtags_combine_returns_deduped(ensure_db, client):
    r = await client.post('/lists', params={'name': 'lh-list-3'})
    lst = r.json()
    t1 = (await client.post('/todos', json={'text': 't1', 'list_id': lst['id']})).json()
    # add overlapping tags
    await client.post(f"/lists/{lst['id']}/hashtags", params={'tag': 'x'})
    await client.post(f"/todos/{t1['id']}/hashtags", params={'tag': 'x'})
    await client.post(f"/todos/{t1['id']}/hashtags", params={'tag': 'y'})

    g = await client.get(
        f"/lists/{lst['id']}/hashtags",
        params={'include_todo_tags': '1', 'combine': '1'},
    )
    assert g.status_code == 200
    body = g.json()
    assert 'hashtags' in body
    # combined should contain x and y once (normalized)
    assert set(body['hashtags']) == set(['#x', '#y'])
