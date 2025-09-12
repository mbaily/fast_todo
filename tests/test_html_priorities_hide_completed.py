import pytest
from app.auth import create_csrf_token


@pytest.mark.asyncio
async def test_priorities_hide_completed_lists(client, ensure_db):
    # Create two lists: one incomplete, one completed; both get a priority
    resp = await client.post('/lists', params={'name': 'visible-list'})
    assert resp.status_code in (200, 201)
    visible = resp.json()
    vid = visible.get('id')
    assert vid

    resp = await client.post('/lists', params={'name': 'completed-list'})
    assert resp.status_code in (200, 201)
    comp = resp.json()
    cid = comp.get('id')
    assert cid

    # Set priorities for both lists (requires CSRF token)
    csrf = create_csrf_token('testuser')
    r = await client.post(
        f'/html_no_js/lists/{vid}/priority',
        data={'priority': '5', '_csrf': csrf},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    r2 = await client.post(
        f'/html_no_js/lists/{cid}/priority',
        data={'priority': '6', '_csrf': csrf},
        follow_redirects=False,
    )
    assert r2.status_code in (302, 303)

    # Mark the second list completed via API
    r3 = await client.post(f'/lists/{cid}/complete', data={'completed': '1'})
    assert r3.status_code == 200

    # 1) Default behavior: cookie absent => hide_completed True =>
    # completed list should NOT appear
    r4 = await client.get('/html_no_js/priorities')
    assert r4.status_code == 200
    text = r4.text
    assert 'visible-list' in text
    assert 'completed-list' not in text

    # 2) Explicit show completed via cookie value '0' => completed list
    # should appear
    r5 = await client.get(
        '/html_no_js/priorities',
        cookies={'priorities_hide_completed': '0'},
    )
    assert r5.status_code == 200
    text2 = r5.text
    assert 'visible-list' in text2
    assert 'completed-list' in text2

    # 3) Explicit hide via cookie '1' => completed list hidden
    r6 = await client.get(
        '/html_no_js/priorities',
        cookies={'priorities_hide_completed': '1'},
    )
    assert r6.status_code == 200
    text3 = r6.text
    assert 'visible-list' in text3
    assert 'completed-list' not in text3
