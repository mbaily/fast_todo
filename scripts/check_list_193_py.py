#!/usr/bin/env python3
import sqlite3
import json

DB='fast_todo.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

out = {}
# list 193
cur.execute('SELECT id,name,priority,completed,parent_list_id FROM liststate WHERE id=?',(193,))
row = cur.fetchone()
out['list_193'] = dict(row) if row is not None else None
# todos in list 193
cur.execute('SELECT id,list_id,priority,created_at FROM todo WHERE list_id=? ORDER BY priority DESC NULLS LAST, created_at DESC',(193,))
rows = cur.fetchall()
out['todos_in_193'] = [dict(r) for r in rows]
# specific todo 449
cur.execute('SELECT id,list_id,priority,created_at FROM todo WHERE id=?',(449,))
r=cur.fetchone()
out['todo_449'] = dict(r) if r else None
# completion rows for todo 449
cur.execute('SELECT todo_id,completion_type_id,done FROM todocompletion WHERE todo_id=?',(449,))
out['todo_449_completions'] = [dict(r) for r in cur.fetchall()]
# completion types
cur.execute('SELECT id,name FROM completiontype')
out['completion_types'] = [dict(r) for r in cur.fetchall()]

# compute highest uncompleted todo priority in list 193
highest = None
for t in out['todos_in_193']:
    tid = t['id']
    pri = t['priority']
    # determine default completion type id if present

# find default completion type
cur.execute("SELECT id FROM completiontype WHERE name='default'")
defrow = cur.fetchone()
defid = defrow['id'] if defrow else None
completed_ids = set()
if defid is not None:
    cur.execute('SELECT todo_id FROM todocompletion WHERE completion_type_id=? AND done=1',(defid,))
    completed_ids = set(r['todo_id'] for r in cur.fetchall())

for t in out['todos_in_193']:
    tid = t['id']
    pri = t['priority']
    if tid in completed_ids:
        continue
    if pri is None:
        continue
    try:
        pv = int(pri)
    except Exception:
        continue
    if highest is None or pv > highest:
        highest = pv

out['highest_uncompleted_priority_in_193'] = highest

print(json.dumps(out, indent=2, default=str))
conn.close()
