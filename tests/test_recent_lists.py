import asyncio
import pytest
from sqlmodel import select
from app.models import RecentListVisit, ListState, User
from app.db import async_session
import uuid


@pytest.mark.asyncio
async def test_recent_list_visit_model_import(ensure_db):
    async with async_session() as sess:
        u = User(username=f"rtest-{uuid.uuid4().hex[:8]}", password_hash="x")
        sess.add(u)
        await sess.commit()
        await sess.refresh(u)
        lst = ListState(name="recent-test", owner_id=u.id)
        sess.add(lst)
        await sess.commit()
        await sess.refresh(lst)
        v = RecentListVisit(user_id=u.id, list_id=lst.id)
        sess.add(v)
        await sess.commit()
        q = await sess.exec(select(RecentListVisit).where(RecentListVisit.user_id == u.id))
        res = q.all()
        assert len(res) == 1
        assert res[0].list_id == lst.id


@pytest.mark.asyncio
async def test_record_visit_and_recent_endpoint(client, ensure_db):
    """Integration-style test hitting the endpoints using the test client."""
    # create user and list via DB session
    async with async_session() as sess:
        u = User(username=f"visit-user-{uuid.uuid4().hex[:8]}", password_hash="x")
        sess.add(u)
        await sess.commit()
        await sess.refresh(u)
        l1 = ListState(name="L1", owner_id=u.id)
        l2 = ListState(name="L2", owner_id=u.id)
        sess.add_all([l1, l2])
        await sess.commit()
        await sess.refresh(l1)
        await sess.refresh(l2)
        user_id = u.id
        l1_id = l1.id
        l2_id = l2.id

    # helper to login and get auth token (project test helpers may differ)
    # The project exposes a test client fixture `client` configured to bypass auth
    # but we'll use the existing session cookie approach: create a session row.
    # Create a server-side session token for the user so the client can send cookie.
    from app.models import Session as SessionModel
    token = f"test-session-token-visit-{uuid.uuid4().hex[:8]}"
    async with async_session() as sess:
        s = SessionModel(session_token=token, user_id=user_id)
        sess.add(s)
        await sess.commit()

    # remove any existing Authorization header so the request uses the
    # cookie-based session lookup (the test client fixture auto-sets a
    # bearer token for a different test user which would otherwise take
    # precedence and cause a forbidden error).
    client.headers.pop('Authorization', None)
    # set cookie on client
    client.cookies.set('session_token', token)

    # record a visit to l1
    r = await client.post(f"/lists/{l1_id}/visit")
    assert r.status_code == 200
    data = r.json()
    assert data.get('list_id') == l1_id
    assert 'visited_at' in data

    # record a visit to l2, then l1 again to update timestamp
    await asyncio.sleep(0.01)
    r2 = await client.post(f"/lists/{l2_id}/visit")
    assert r2.status_code == 200
    await asyncio.sleep(0.01)
    r3 = await client.post(f"/lists/{l1_id}/visit")
    assert r3.status_code == 200

    # fetch recent lists; with top-N semantics (preserve relative order for items
    # already in the top-N), visiting a top-N item again does not move it to
    # the top. After the sequence L1, L2, L1 the expected top remains L2.
    r4 = await client.get('/lists/recent')
    assert r4.status_code == 200
    recent = r4.json()
    ids = [it['id'] for it in recent]
    assert ids[0] == l2_id
    assert l1_id in ids


@pytest.mark.asyncio
async def test_visit_forbidden_for_other_user(client, ensure_db):
    # user A owns a private list, user B should be forbidden from recording visit
    async with async_session() as sess:
        a = User(username=f'owner-a-{uuid.uuid4().hex[:8]}', password_hash='x')
        b = User(username=f'other-b-{uuid.uuid4().hex[:8]}', password_hash='x')
        sess.add_all([a, b])
        await sess.commit()
        await sess.refresh(a)
        await sess.refresh(b)
        lst = ListState(name='secret', owner_id=a.id)
        sess.add(lst)
        await sess.commit()
        await sess.refresh(lst)
        lst_id = lst.id
        b_id = b.id
    # create session for B
    from app.models import Session as SessionModel
    token = f'session-b-{uuid.uuid4().hex[:8]}'
    async with async_session() as sess:
        s = SessionModel(session_token=token, user_id=b_id)
        sess.add(s)
        await sess.commit()
    client.cookies.set('session_token', token)
    r = await client.post(f"/lists/{lst_id}/visit")
    assert r.status_code == 403
