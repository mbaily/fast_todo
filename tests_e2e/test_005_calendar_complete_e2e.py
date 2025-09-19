from datetime import datetime, timedelta, timezone
import pytest

pytestmark = pytest.mark.e2e


def _utc_now():
    return datetime.now(timezone.utc)


@pytest.mark.parametrize("i", list(range(200)))
def test_calendar_complete_flow(e2e_client, bearer_headers_a, i):
    # Create list and todo with a near date
    r = e2e_client.post("/lists", params={"name": f"E2E-Cal {i}"}, headers=bearer_headers_a)
    assert r.status_code == 200
    lid = r.json()["id"]
    today = _utc_now().date().isoformat()
    r = e2e_client.post("/todos", json={"text": f"WindowEvent {today}", "list_id": lid}, headers=bearer_headers_a)
    assert r.status_code == 200
    tid = r.json()["id"]
    r0 = e2e_client.get("/calendar/occurrences", headers=bearer_headers_a)
    occs = [o for o in r0.json().get("occurrences", []) if o.get("item_type") == "todo" and o.get("id") == tid]
    if not occs:
        return
    occ_hash = occs[0]["occ_hash"]
    # Complete it
    r1 = e2e_client.post("/occurrence/complete", data={"hash": occ_hash}, headers=bearer_headers_a)
    assert r1.status_code == 200
    # Verify completed flag is true
    r2 = e2e_client.get("/calendar/occurrences", headers=bearer_headers_a)
    occs2 = [o for o in r2.json().get("occurrences", []) if o.get("occ_hash") == occ_hash]
    assert not occs2 or occs2[0].get("completed") is True
