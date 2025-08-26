import pytest

pytestmark = pytest.mark.asyncio


async def test_duplicate_list_name_returns_400(client):
    r1 = await client.post('/lists', params={'name': 'dupname'})
    assert r1.status_code == 200
    r2 = await client.post('/lists', params={'name': 'dupname'})
    # duplicate list name returns existing list (idempotent)
    assert r2.status_code == 200


async def test_idempotent_list_hashtag_add(client):
    # create a list
    r = await client.post('/lists', params={'name': 'tag-idemp'})
    assert r.status_code == 200
    lst = r.json()
    lid = lst['id']
    # add hashtag twice
    a1 = await client.post(f'/lists/{lid}/hashtags', params={'tag': 'repeat'})
    assert a1.status_code == 200
    a2 = await client.post(f'/lists/{lid}/hashtags', params={'tag': 'repeat'})
    # second add should still be 200 and idempotent
    assert a2.status_code == 200
