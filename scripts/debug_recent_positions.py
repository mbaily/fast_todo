import asyncio
import uuid
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import async_session
from app.models import User, ListState, Session as SessionModel

async def main():
    async with async_session() as sess:
        u = User(username=f'dbg-{uuid.uuid4().hex[:8]}', password_hash='x')
        sess.add(u)
        await sess.commit()
        await sess.refresh(u)
        l1 = ListState(name='DBG1', owner_id=u.id)
        l2 = ListState(name='DBG2', owner_id=u.id)
        l3 = ListState(name='DBG3', owner_id=u.id)
        sess.add_all([l1, l2, l3])
        await sess.commit()
        await sess.refresh(l1)
        await sess.refresh(l2)
        await sess.refresh(l3)
        token = f'dbg-token-{uuid.uuid4().hex[:8]}'
        s = SessionModel(session_token=token, user_id=u.id)
        sess.add(s)
        await sess.commit()
        user_id = u.id
        l1_id = l1.id
        l2_id = l2.id
        l3_id = l3.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        # set cookie for session lookup
        client.cookies.set('session_token', token)
        # visit L1
        r = await client.post(f'/lists/{l1_id}/visit')
        print('visit L1:', r.status_code, r.json())
        # visit L2
        r = await client.post(f'/lists/{l2_id}/visit')
        print('visit L2:', r.status_code, r.json())
        # visit L3
        r = await client.post(f'/lists/{l3_id}/visit')
        print('visit L3:', r.status_code, r.json())
        # re-visit L1
        r = await client.post(f'/lists/{l1_id}/visit')
        print('re-visit L1:', r.status_code, r.json())
        # fetch recent rows via DB
        async with async_session() as sess:
            q = await sess.exec("SELECT user_id, list_id, visited_at, position FROM recentlistvisit WHERE user_id = :uid ORDER BY position IS NULL, position ASC, visited_at DESC", {'uid': user_id})
            rows = q.fetchall()
            print('\nDB recentlistvisit rows:')
            for row in rows:
                print(row)
        # call GET /lists/recent
        r = await client.get('/lists/recent')
        print('\nGET /lists/recent status:', r.status_code)
        print('payload:', r.json())

if __name__ == '__main__':
    asyncio.run(main())
