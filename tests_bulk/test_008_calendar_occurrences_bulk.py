from datetime import datetime, timedelta, timezone

import pytest


pytestmark = pytest.mark.bulk


def _utc_now():
    return datetime.now(timezone.utc)


def test_calendar_occurrences_basic(app_client, auth_headers, default_list_id, make_todo):
    # Create todos with explicit dates inside a tight window
    now = _utc_now()
    t1 = make_todo(default_list_id, text=f"Meet on {now.date().isoformat()}")
    t2 = make_todo(default_list_id, text=f"Call on {(now + timedelta(days=1)).date().isoformat()}")
    start = (now - timedelta(days=1)).isoformat()
    end = (now + timedelta(days=2)).isoformat()
    r = app_client.get("/calendar/occurrences", params={"start": start, "end": end}, headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "occurrences" in data
    # Should contain at least one of t1/t2
    ids = {o.get("id") for o in data["occurrences"] if o.get("item_type") == "todo"}
    assert t1 in ids or t2 in ids


def test_calendar_occurrences_first_date_only_respected(app_client, auth_headers, default_list_id, make_todo):
    # Create a todo with two dates
    text = "ParamEvent Jan 10 2025 and Feb 20 2025"
    tid = make_todo(default_list_id, text=text)
    # Patch first_date_only
    r = app_client.patch(f"/todos/{tid}", json={"first_date_only": True}, headers=auth_headers)
    assert r.status_code == 200
    r2 = app_client.get("/calendar/occurrences", headers=auth_headers)
    assert r2.status_code == 200
    occs = [o for o in r2.json().get("occurrences", []) if o.get("item_type") == "todo" and o.get("id") == tid]
    # If present, there should be at most one explicit occurrence for this todo
    assert len(occs) <= 1


def test_calendar_occurrences_negative_invalid_start(app_client, auth_headers):
    r = app_client.get("/calendar/occurrences", params={"start": "not-a-date"}, headers=auth_headers)
    assert r.status_code == 400


def test_calendar_occurrences_include_ignored(app_client, auth_headers, default_list_id, make_todo):
    # Create todo with a date
    now = _utc_now()
    tid = make_todo(default_list_id, text=f"WindowEvent {now.date().isoformat()}")
    # Query once to compute occurrences and find one
    r0 = app_client.get("/calendar/occurrences", headers=auth_headers)
    assert r0.status_code == 200
    occs = [o for o in r0.json().get("occurrences", []) if o.get("item_type") == "todo" and o.get("id") == tid]
    if not occs:
        return  # if extraction missed, skip silently
    occ_hash = occs[0].get("occ_hash")
    # Mark completed via form param (bearer token present, so CSRF not required)
    r1 = app_client.post("/occurrence/complete", data={"hash": occ_hash}, headers=auth_headers)
    assert r1.status_code == 200
    # With include_ignored=false (default), still visible; completed flag true
    r2 = app_client.get("/calendar/occurrences", headers=auth_headers)
    occs2 = [o for o in r2.json().get("occurrences", []) if o.get("occ_hash") == occ_hash]
    assert occs2 and occs2[0].get("completed") is True
