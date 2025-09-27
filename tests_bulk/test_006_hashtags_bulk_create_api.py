import pytest

pytestmark = pytest.mark.bulk


def test_bulk_create_hashtags_basic(app_client, auth_headers, make_list):
    # Ensure initial fetch returns no special staging list hashtags
    r = app_client.post('/hashtags/bulk_create/json', json={'tags': '#alpha #beta #beta #gamma'}, headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data['ok'] is True
    assert set(data['created']) >= {'#alpha', '#beta', '#gamma'}
    assert data['existing'] == [] or isinstance(data['existing'], list)
    # second call should treat them as existing
    r2 = app_client.post('/hashtags/bulk_create/json', json={'tags': '#alpha #beta'}, headers=auth_headers)
    assert r2.status_code == 200
    d2 = r2.json()
    assert set(d2['existing']) >= {'#alpha', '#beta'}


def test_bulk_create_hashtags_list_visibility(app_client, auth_headers):
    # Create tags
    r = app_client.post('/hashtags/bulk_create/json', json={'tags': '#vis1 #vis2'}, headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    # list_id is deprecated (was a staging list); may now be None
    assert 'list_id' in data
    # The tags should be visible on hashtags page HTML (basic smoke check for one tag text)
    r2 = app_client.get('/html_no_js/hashtags', headers=auth_headers)
    assert r2.status_code == 200
    txt = r2.text
    assert '#vis1' in txt or '#vis2' in txt


def test_bulk_create_invalid_tokens(app_client, auth_headers):
    # invalid: starting with digit or empty
    r = app_client.post('/hashtags/bulk_create/json', json={'tags': '#ok 123bad ### #AlsoOk'}, headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert '#ok' in data['all']
    assert '#alsook' in data['all']  # normalized to lowercase
    # The raw invalid tokens should include something like '123bad' or '###'
    assert any(tok in data['invalid'] for tok in ['123bad', '###'])
