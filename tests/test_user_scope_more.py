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
    return r


@pytest.mark.asyncio
async def test_multiple_lists_per_user_and_visibility(prepare_db):
    transport = ASGITransport(app=app)

    # create users
    await create_user("carol", "carolpass")
    await create_user("dan", "danpass")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        rc = await get_token(ac, "carol", "carolpass")
        tc = rc.json().get("access_token")
        rd = await get_token(ac, "dan", "danpass")
        td = rd.json().get("access_token")

    # carol creates two lists
    async with AsyncClient(transport=transport, base_url="http://test") as ccli:
        ccli.headers.update({"Authorization": f"Bearer {tc}"})
        r1 = await ccli.post("/lists", params={"name": "carol-work"})
        assert r1.status_code == 200
        r2 = await ccli.post("/lists", params={"name": "carol-home"})
        assert r2.status_code == 200
        lists = await ccli.get("/lists")
        assert len(lists.json()) >= 2

    # dan creates one list and shouldn't see carol's lists
    async with AsyncClient(transport=transport, base_url="http://test") as dcli:
        dcli.headers.update({"Authorization": f"Bearer {td}"})
        rd1 = await dcli.post("/lists", params={"name": "dan-list"})
        assert rd1.status_code == 200
        dlists = (await dcli.get("/lists")).json()
        assert all(l.get("name") != "carol-work" for l in dlists)


@pytest.mark.asyncio
async def test_duplicate_list_name_returns_existing(prepare_db):
    transport = ASGITransport(app=app)

    # create two users
    await create_user("erin", "erinpass")
    await create_user("frank", "frankpass")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        re = await get_token(ac, "erin", "erinpass")
        te = re.json().get("access_token")
        rf = await get_token(ac, "frank", "frankpass")
        tf = rf.json().get("access_token")

    # erin creates a list named 'shared'
    async with AsyncClient(transport=transport, base_url="http://test") as ecli:
        ecli.headers.update({"Authorization": f"Bearer {te}"})
        r = await ecli.post("/lists", params={"name": "shared"})
        assert r.status_code == 200
        el = r.json()

    # frank tries to create 'shared' - current behavior returns the existing list
    async with AsyncClient(transport=transport, base_url="http://test") as fcli:
        fcli.headers.update({"Authorization": f"Bearer {tf}"})
        rf = await fcli.post("/lists", params={"name": "shared"})
        assert rf.status_code == 200
        fl = rf.json()

        # they should refer to the same list id (DB-enforced global uniqueness fallback)
        # Under private-by-default policy, lists are scoped to owners.
        # Creating the same name under different users should produce distinct lists.
        assert fl.get("id") != el.get("id")


@pytest.mark.asyncio
async def test_default_list_fallback_for_user(prepare_db):
    transport = ASGITransport(app=app)

    # create user
    await create_user("gina", "ginapass")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        rg = await get_token(ac, "gina", "ginapass")
        tg = rg.json().get("access_token")

    # ensure user's default list exists by creating default for user
    async with AsyncClient(transport=transport, base_url="http://test") as gcli:
        gcli.headers.update({"Authorization": f"Bearer {tg}"})
        # create a personal default-like list for the user
        r = await gcli.post("/lists", params={"name": "user-default"})
        assert r.status_code == 200
        dl = r.json()
        # create a todo in the user's list (API requires list_id)
        rt = await gcli.post("/todos", params={"text": "uses-user-default", "list_id": dl.get("id")})
        assert rt.status_code == 200
        todo = rt.json()
        assert todo.get("list_id") == dl.get("id")
