import sqlite3, json
DB='fast_todo.db'
LIST_ID=190

def get_default_completion_type_id(cur):
    cur.execute("SELECT id FROM completiontype WHERE name = 'default' LIMIT 1")
    r = cur.fetchone()
    return r[0] if r else None

conn=sqlite3.connect(DB)
cur=conn.cursor()
# find immediate sublists
cur.execute('SELECT id, priority, completed FROM liststate WHERE parent_list_id = ?',(LIST_ID,))
subs = cur.fetchall()
sub_ids = [r[0] for r in subs]
print('sublists:', subs)
# prefetch child list priorities (immediate children of those sublists)
child_map = {}
if sub_ids:
    q = 'SELECT id, priority, parent_list_id, completed FROM liststate WHERE parent_list_id IN ({})'.format(','.join('?'*len(sub_ids)))
    cur.execute(q, sub_ids)
    for sid, spri, parent_id, scomp in cur.fetchall():
        child_map.setdefault(parent_id, []).append((sid, spri, bool(scomp)))

def_id = get_default_completion_type_id(cur)
print('default completion_type id =', def_id)

result = []
for sid, spri, scomp in subs:
    max_p = None
    # highest uncompleted todo priority in this sublist
    if def_id is not None:
        cur.execute('''SELECT t.priority FROM todo t
                       LEFT JOIN todocompletion tc ON tc.todo_id = t.id AND tc.completion_type_id = ?
                       WHERE t.list_id = ? AND (tc.todo_id IS NULL OR tc.done = 0) AND t.priority IS NOT NULL
                       ORDER BY t.priority DESC LIMIT 1''',(def_id, sid))
        row = cur.fetchone()
    else:
        cur.execute('SELECT t.priority FROM todo t WHERE t.list_id = ? AND t.priority IS NOT NULL ORDER BY t.priority DESC LIMIT 1',(sid,))
        row = cur.fetchone()
    if row:
        try:
            max_p = int(row[0])
        except Exception:
            max_p = None
    # include immediate child lists' own priority
    for child_sid, child_pri, child_comp in child_map.get(sid, []):
        if child_pri is None:
            continue
        if child_comp:
            continue
        try:
            cp = int(child_pri)
        except Exception:
            continue
        if max_p is None or cp > max_p:
            max_p = cp
    result.append({'sublist_id': sid, 'list_priority': spri, 'computed_override': max_p, 'completed': bool(scomp)})

print(json.dumps(result, indent=2))
conn.close()
