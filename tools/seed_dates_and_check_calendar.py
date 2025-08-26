"""Seed many todos with various date formats and check calendar occurrences.

This script uses FastAPI's TestClient to exercise endpoints without running
an external server. It logs counts of detected occurrences.
"""
from fastapi.testclient import TestClient
from app.main import app
import pprint

client = TestClient(app)

# login as testuser (conftest ensures this user exists with password 'testpass')
resp = client.post('/auth/token', json={'username': 'testuser', 'password': 'testpass'})
if resp.status_code != 200:
    print('failed to get token:', resp.status_code, resp.text)
    raise SystemExit(1)
token = resp.json().get('access_token')
client.headers.update({'Authorization': f'Bearer {token}'})

# ensure a list exists and set it as default
r = client.post('/lists', params={'name': 'Calendar Seed List'})
if r.status_code != 200:
    print('failed to create list', r.status_code, r.text)
    raise SystemExit(1)
list_row = r.json()
list_id = list_row.get('id')
print('using list_id', list_id)

client.post(f'/server/default_list/{list_id}')

samples = []
# numeric 2-token US-style (M/D)
samples += [f'Starfield {m}/{d}' for m,d in [('8','23'), ('9','5'), ('2','29'), ('11','11')]]
# numeric 2-token D/M (day first)
samples += [f'Remix {d}/{m}' for d,m in [('23','8'), ('5','9'), ('22','8')]]
# 3-token numeric (with year)
samples += [f'Spacewalk {m}/{d}/2025' for m,d in [('8','23'), ('9','05')]]
# english month names
samples += ['Launch August 23', 'Launch 23 August', 'Launch Aug 23', 'Party 1st Sept', 'Meeting September 5']
# short numeric tokens that should not be parsed as dates
samples += ['Note eight', 'Number 8', 'Check 12']
# mixed context
samples += ['Plan Starfield Aug 23 at office', 'Starfield 8/23 extra notes']

created = []
for s in samples:
    r = client.post('/todos', params={'text': s, 'list_id': list_id})
    if r.status_code == 200:
        created.append(s)
    else:
        print('failed to create todo for', s, r.status_code, r.text)

print('created todos:', len(created))

# query occurrences for next 120 days
from datetime import datetime, timezone, timedelta
now = datetime.now(timezone.utc)
start = now.isoformat()
end = (now + timedelta(days=120)).isoformat()
resp = client.get('/calendar/occurrences', params={'start': start, 'end': end})
if resp.status_code != 200:
    print('failed to get occurrences', resp.status_code, resp.text)
    raise SystemExit(1)
occ = resp.json().get('occurrences', [])
print('total occurrences returned:', len(occ))

# match occurrences back to created samples by title substring
matches = {s: 0 for s in created}
for o in occ:
    title = o.get('title') or ''
    for s in created:
        if s.split()[0] in title or s in title:
            matches[s] += 1

pprint.pprint(matches)

# summary counts
found = sum(1 for v in matches.values() if v > 0)
print(f'{found} of {len(created)} todos produced at least one occurrence')
print('done')
