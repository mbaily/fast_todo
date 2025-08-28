import pytest
from sqlmodel import select


@pytest.mark.asyncio
async def test_category_sort_flag_roundtrip(client, ensure_db):
    # create a category
    resp = await client.post('/api/categories', json={'name': 'Zed', 'position': 0})
    assert resp.status_code == 200
    cat = resp.json()
    cat_id = cat.get('id')

    # initial GET should include sort_alphanumeric (default False)
    resp = await client.get('/api/categories')
    assert resp.status_code == 200
    cats = resp.json().get('categories', [])
    found = [c for c in cats if c.get('id') == cat_id]
    assert len(found) == 1
    assert found[0].get('sort_alphanumeric') in (False, None) or found[0].get('sort_alphanumeric') is False

    # set the flag true
    resp = await client.post(f'/api/categories/{cat_id}/sort', json={'sort': True})
    assert resp.status_code == 200
    data = resp.json()
    assert data.get('ok') is True
    assert data.get('sort_alphanumeric') is True

    # GET should now show the flag true
    resp = await client.get('/api/categories')
    cats = resp.json().get('categories', [])
    found = [c for c in cats if c.get('id') == cat_id]
    assert len(found) == 1
    assert found[0].get('sort_alphanumeric') is True
