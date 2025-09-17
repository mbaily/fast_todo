import pytest
import requests
import time
from test_pwa.client import PwaClient
from test_pwa import local_store, config

# mark this module as pwa so it is skipped in normal test runs
pytestmark = pytest.mark.pwa


try:
    SERVER_CFG = config.load_config()
except Exception:
    # If config isn't fully available during collection, fall back to defaults
    SERVER_CFG = {}

SERVER = SERVER_CFG.get("url", "https://0.0.0.0:10443")
USERNAME = SERVER_CFG.get("username", "mbaily")
PASSWORD = SERVER_CFG.get("password", "mypass")


def server_available() -> bool:
    try:
        resp = requests.get(SERVER, verify=False, timeout=2)
        return resp.status_code < 500
    except Exception:
        return False


@pytest.fixture(autouse=True)
def clear_db():
    local_store.clear_all()
    yield
    local_store.clear_all()


def test_full_sync_flow():
    if not server_available():
        pytest.skip("Dev server not available")
    client = PwaClient.from_config(SERVER_CFG)
    assert client.login(USERNAME, PASSWORD)
    # initial fetch
    todos = client.fetch_all()
    assert isinstance(todos, list)

    # create a todo via queued add and sync
    tid = f"test-{int(time.time()*1000)}"
    payload = {"client_id": tid, "text": "Integration Test", "note": "from pytest", "list_id": 1}
    local_store.queue_change("add_todo", str(payload))
    res = client.sync()
    assert "synced" in res

    # create via create_todo op directly
    ops = [{"op": "create_todo", "payload": {"client_id": "c1", "text": "Direct create", "note": "op", "list_id": 1}}]
    r = client.session.post(f"{SERVER}/sync", json={"ops": ops}, verify=client.verify)
    assert r.status_code == 200

    # update a todo: fetch latest list, pick one, queue edit, sync
    client.fetch_all()
    rows = local_store.get_all_todos()
    if rows:
        first = rows[0]
        edit_payload = {"id": first.get("id"), "text": "Updated from test", "note": "edited"}
        local_store.queue_change("edit_todo", str(edit_payload))
        r2 = client.sync()
        assert "synced" in r2

    # delete a todo
    client.fetch_all()
    rows = local_store.get_all_todos()
    if rows:
        first = rows[0]
        local_store.queue_change("delete_todo", str({"id": first.get("id")}))
        r3 = client.sync()
        assert "synced" in r3
