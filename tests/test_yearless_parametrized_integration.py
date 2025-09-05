import pytest
from datetime import datetime, timezone
from sqlmodel import select
from app.db import async_session
from app.models import Todo, ListState


cases = [
    # (month, day, created_at, expected_single_year)
    (1, 2, datetime(2025, 12, 20, tzinfo=timezone.utc), 2026),
    (1, 2, datetime(2025, 1, 2, tzinfo=timezone.utc), 2025),
    (12, 31, datetime(2025, 12, 31, tzinfo=timezone.utc), 2025),
    # With global 1-year cap, Feb 29 cannot resolve to the next leap year; expect no occurrence
    (2, 29, datetime(2025, 6, 1, tzinfo=timezone.utc), None),
    # same-day but created later than event time (midday) -> next year
    (1, 2, datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc), 2026),
    # created on Dec 31 and target Jan 1 -> next year
    (1, 1, datetime(2025, 12, 31, tzinfo=timezone.utc), 2026),
    # created after leap day in leap year -> next available leap year
    (2, 29, datetime(2028, 3, 1, tzinfo=timezone.utc), None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("month,day,created_at,expected_year", cases)
async def test_param_integration_calendar_resolution(client, month, day, created_at, expected_year):
    # create list
    r = await client.post('/lists', params={'name': f'Param List {month}-{day}'})
    assert r.status_code == 200
    lid = r.json().get('id')
    # Use a portable month-abbrev + day formatting (Windows lacks '%-d')
    month_abbr = datetime(2000, month, day).strftime("%b")
    text = f"ParamEvent {month_abbr} {day}"
    r = await client.post('/todos', json={'text': text, 'list_id': lid})
    assert r.status_code == 200
    tid = r.json().get('id')

    # override created_at (commit while session is open so change persists)
    async with async_session() as sess:
        q = await sess.exec(select(Todo).where(Todo.id == tid))
        t = q.first()
        t.created_at = created_at
        sess.add(t)
        await sess.commit()

    # query a window covering the expected year. If expected_year is None (no
    # candidate due to 1-year cap), query the original todo created_at..+1yr
    # window to confirm absence.
    if expected_year is None:
        start_dt = created_at
        end_dt = datetime(created_at.year + 1, created_at.month, created_at.day, tzinfo=timezone.utc)
    else:
        start_dt = datetime(expected_year, 1, 1, tzinfo=timezone.utc)
        end_dt = datetime(expected_year, 12, 31, tzinfo=timezone.utc)
    start = start_dt.isoformat()
    end = end_dt.isoformat()
    resp = await client.get('/calendar/occurrences', params={'start': start, 'end': end})
    assert resp.status_code == 200
    occ = resp.json().get('occurrences', [])
    if expected_year is None:
        # Expect no occurrences within that far-future year when cap prevents resolution
        found = [o for o in occ if o['item_type'] == 'todo' and o['id'] == tid]
        assert not found, f'did not expect occurrences for {month}/{day} when expected_year is None'
    else:
        found = [o for o in occ if o['item_type'] == 'todo' and o['id'] == tid and o['occurrence_dt'].startswith(str(expected_year))]
        assert found, f'expected occurrence in {expected_year} for {month}/{day}'
