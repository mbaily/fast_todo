"""Diagnostic: aggregate calendar occurrences per todo and list recurrence info."""
from fastapi.testclient import TestClient
from app.main import app
from datetime import datetime, timezone, timedelta
import collections
import pprint

client = TestClient(app)
# authenticate
resp = client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
if resp.status_code != 200:
    print('auth failed', resp.status_code, resp.text)
    raise SystemExit(1)
client.headers.update({'Authorization': f"Bearer {resp.json().get('access_token')}"})

# count lists and todos
r = client.get('/lists')
lists = r.json() if r.status_code == 200 else []
print('lists for user:', len(lists))

# fetch todos in each list
all_todos = []
for lst in lists:
    lid = lst.get('id')
    rr = client.get(f'/lists/{lid}')
    if rr.status_code == 200:
        data = rr.json()
        # the list endpoint returns list metadata and todos? if not, query DB via calendar occurrences
        pass

now = datetime.now(timezone.utc)
start = now.isoformat()
end = (now + timedelta(days=365)).isoformat()
print('querying occurrences for next 365 days')
resp = client.get('/calendar/occurrences', params={'start': start, 'end': end, 'max_total': 20000})
if resp.status_code != 200:
    print('failed to fetch occurrences', resp.status_code, resp.text)
    raise SystemExit(1)
occ = resp.json().get('occurrences', [])
print('total occurrences:', len(occ))

# aggregate by item id
counts = collections.Counter()
by_item = {}
for o in occ:
    key = (o.get('item_type'), o.get('id'))
    counts[key] += 1
    by_item.setdefault(key, []).append(o)

# show top contributors
print('\nTop 20 contributors:')
for (typ, iid), cnt in counts.most_common(20):
    print(f'{typ} {iid}: {cnt}')
    sample = by_item[(typ, iid)][0]
    pprint.pprint(sample)

# find todos with recurrence set
print('\nTodos with recurrence metadata:')
from app.db import async_session
from sqlmodel import select
from app.models import Todo

import asyncio

async def _fetch_recurring():
    async with async_session() as sess:
        q = await sess.exec(select(Todo).where(Todo.recurrence_rrule != None))
        rows = q.all()
        return rows

rows = asyncio.get_event_loop().run_until_complete(_fetch_recurring())
print('recurring todos count:', len(rows))
for t in rows:
    print('todo id', t.id, 'text', t.text, 'rrule', t.recurrence_rrule)

print('done')
