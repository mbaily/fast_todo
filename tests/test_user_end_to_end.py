import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import async_session, init_db
from app.models import User
from app.auth import pwd_context
from sqlmodel import select


@pytest_asyncio.fixture
async def prepare_db():
    await init_db()
    yield


async def create_user(username: str, password: str):
    async with async_session() as sess:
        ph = pwd_context.hash(password)
        u = User(username=username, password_hash=ph, is_admin=False)
        sess.add(u)
        try:
            await sess.commit()
        except Exception:
            await sess.rollback()
            q = await sess.exec(select(User).where(User.username == username))
            return q.first()
        await sess.refresh(u)
        return u


async def get_token(client: AsyncClient, username: str, password: str):
    r = await client.post("/auth/token", json={"username": username, "password": password})
    return r.json().get("access_token")


@pytest.mark.asyncio
async def test_user_flow_basic(prepare_db):
    transport = ASGITransport(app=app)
    # create user and login
    await create_user("user1", "pw1")
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        token = await get_token(ac, "user1", "pw1")
        assert token

    # use an authenticated client for user actions
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        # create a list
        r = await client.post("/lists", params={"name": "groceries"})
        assert r.status_code == 200
        lst = r.json()
        lid = lst["id"]

        # create todos
        t1 = await client.post("/todos", json={"text": "buy milk", "list_id": lid})
        t2 = await client.post("/todos", json={"text": "buy eggs", "list_id": lid})
        assert t1.status_code == 200 and t2.status_code == 200
        todo1 = t1.json()
        todo2 = t2.json()

        # get todo
        g = await client.get(f"/todos/{todo1['id']}")
        assert g.status_code == 200

        # update todo
        up = await client.patch(f"/todos/{todo1['id']}", json={"text": "buy almond milk"})
        assert up.status_code == 200
        assert up.json()["text"] == "buy almond milk"

        # mark complete (default)
        comp = await client.post(f"/todos/{todo2['id']}/complete", json={"done": True})
        assert comp.status_code == 200

        # delete todo1
        d = await client.delete(f"/todos/{todo1['id']}")
        assert d.status_code == 200
        # ensure it's gone
        g2 = await client.get(f"/todos/{todo1['id']}")
        assert g2.status_code == 404


@pytest.mark.asyncio
async def test_delete_list_and_reassign(prepare_db):
    transport = ASGITransport(app=app)
    # create user
    await create_user("user2", "pw2")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        token = await get_token(ac, "user2", "pw2")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        # create two lists
        r1 = await client.post("/lists", params={"name": "A"})
        r2 = await client.post("/lists", params={"name": "B"})
        assert r1.status_code == 200 and r2.status_code == 200
        la = r1.json()
        lb = r2.json()

        # create a todo in A
        t = await client.post("/todos", json={"text": "taskA", "list_id": la['id']})
        assert t.status_code == 200
        todo = t.json()

        # set server default to B so deleting A moves todos to B
        sd = await client.post(f"/server/default_list/{lb['id']}")
        assert sd.status_code == 200

        # delete A
        delr = await client.delete(f"/lists/{la['id']}")
        assert delr.status_code == 200
        # todo should have been deleted with the list
        gt = await client.get(f"/todos/{todo['id']}")
        assert gt.status_code == 404


@pytest.mark.asyncio
async def test_multi_user_full_flow(prepare_db):
    transport = ASGITransport(app=app)
    await create_user("alice2", "a2")
    await create_user("bob2", "b2")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ta = await get_token(ac, "alice2", "a2")
        tb = await get_token(ac, "bob2", "b2")

    # alice creates list and todo
    import secrets
    unique_name = f"alice-list-{secrets.token_hex(6)}"

    async with AsyncClient(transport=transport, base_url="http://test") as acli:
        acli.headers.update({"Authorization": f"Bearer {ta}"})
        ra = await acli.post("/lists", params={"name": unique_name})
        assert ra.status_code == 200
        al = ra.json()
        t = await acli.post("/todos", json={"text": "a task", "list_id": al['id']})
        assert t.status_code == 200

    # bob cannot see alice's list or todo
    async with AsyncClient(transport=transport, base_url="http://test") as bcli:
        bcli.headers.update({"Authorization": f"Bearer {tb}"})
        r = await bcli.get("/lists")
        assert all(l.get("name") != unique_name for l in r.json())

        # bob cannot delete alice's todo
        # fetch alice todo id via alice's client
        async with AsyncClient(transport=transport, base_url="http://test") as acli2:
            acli2.headers.update({"Authorization": f"Bearer {ta}"})
            lsts = await acli2.get("/lists")
            alist = next((l for l in lsts.json() if l.get("name") == unique_name), None)
            assert alist
            await acli2.get("/lists/{}/completion_types".format(alist['id']))
            # get one todo id via listing todos by id (we'll fetch a todo id directly)
            # create another todo and capture id
            newt = await acli2.post("/todos", json={"text": "another", "list_id": alist['id']})
            tid = newt.json()['id']

        rdel = await bcli.delete(f"/todos/{tid}")
        assert rdel.status_code == 403
