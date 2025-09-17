#!/usr/bin/env python3
"""Inspect DB to decide whether list 192 should show a secondary priority 6 deriving
from sublist 190. Uses only Python and sqlite3 per operational rules.
"""
import sqlite3
from pprint import pprint

db = 'fast_todo.db'
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()

def show_tables():
    print('Tables:')
    for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        print('-', r[0])

def show_schema(table):
    print(f"\nSchema for {table}:")
    try:
        rows = list(cur.execute(f"PRAGMA table_info({table})"))
        if not rows:
            print('  (no such table or empty schema)')
            return
        for r in rows:
            # r: cid, name, type, notnull, dflt_value, pk
            print(f"  {r[1]} {r[2]} notnull={r[3]} pk={r[5]} dflt={r[4]}")
    except Exception as e:
        print('  (no such table)')

def fetch_list(lid):
    cur.execute('SELECT * FROM liststate WHERE id=?', (lid,))
    return cur.fetchone()

def fetch_todos_by_list(lid):
    # select id, text, priority and any obvious completion-like columns if present
    cols = [r[1] for r in cur.execute('PRAGMA table_info(todo)')]
    select_cols = ['id', 'text', 'priority']
    if 'completed' in cols:
        select_cols.append('completed')
    q = 'SELECT ' + ','.join(select_cols) + ' FROM todo WHERE list_id=? ORDER BY id'
    cur.execute(q, (lid,))
    return cur.fetchall()

show_tables()
show_schema('liststate')
show_schema('todo')
show_schema('todo_completion')
show_schema('todocompletion')
show_schema('todo_completed')
show_schema('completion_type')

# Determine completed todo ids if there is a todo_completion-like table, otherwise
# try a `completed` boolean column on todo.
completed_ids = set()
found_completion = False
for tname in ['todo_completion', 'todocompletion', 'todo_completed']:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tname,))
    if cur.fetchone():
        print('\nUsing completion table:', tname)
        try:
            q = "SELECT todo_id FROM %s WHERE done=1" % tname
            for r in cur.execute(q):
                completed_ids.add(r[0])
            found_completion = True
            break
        except Exception:
            # try join with completion_type
            try:
                q = "SELECT todo_completion.todo_id FROM todo_completion JOIN completion_type ON completion_type.id=todo_completion.completion_type_id WHERE completion_type.name='default' AND todo_completion.done=1"
                for r in cur.execute(q):
                    completed_ids.add(r[0])
                found_completion = True
                break
            except Exception:
                pass

if not found_completion:
    # Try `completed` column on todo
    try:
        cur.execute("PRAGMA table_info(todo)")
        cols = [r[1] for r in cur.fetchall()]
        if 'completed' in cols:
            print('\nUsing todo.completed column')
            for r in cur.execute('SELECT id FROM todo WHERE completed=1'):
                completed_ids.add(r[0])
    except Exception:
        pass

print('\nCompleted todo ids sample:', sorted(list(completed_ids))[:20])

for lid in (190, 192):
    print('\nList', lid)
    lr = fetch_list(lid)
    pprint(dict(lr) if lr else None)
    print('\nTodos in list', lid)
    try:
        for t in fetch_todos_by_list(lid):
            d = dict(t)
            # if completed column present in todo, include it; otherwise consult completed_ids
            if 'completed' in d:
                d['completed'] = bool(d.get('completed'))
            else:
                d['completed'] = d['id'] in completed_ids
            pprint(d)
    except Exception as e:
        print('  failed to fetch todos:', e)

def highest_uncompleted_priority_for_list(lid, include_immediate_sublists=True):
    # find immediate sublists
    subq = 'SELECT id FROM liststate WHERE parent_list_id=?'
    sub_ids = [r[0] for r in cur.execute(subq, (lid,))]
    list_ids = [lid] + sub_ids if include_immediate_sublists else [lid]
    # fetch todos in these lists
    q = f"SELECT id, priority FROM todo WHERE list_id IN ({','.join(['?']*len(list_ids))})"
    rows = list(cur.execute(q, list_ids))
    maxp = None
    uncompleted = []
    for r in rows:
        tid, pr = r
        done = tid in completed_ids
        if not done:
            uncompleted.append((tid, pr))
            if pr is not None:
                if maxp is None or pr > maxp:
                    maxp = pr
    return maxp, uncompleted, sub_ids

print('\nCompute override priorities:')
for lid in (190, 192):
    maxp, uncompleted, sub_ids = highest_uncompleted_priority_for_list(lid)
    print(f'List {lid} immediate sublists: {sub_ids}')
    print(f'  highest uncompleted todo priority (including immediate sublists): {maxp}')
    print(f'  uncompleted todos (id,priority):')
    for u in uncompleted:
        print('   ', u)


def compute_sublists_override_for_parents():
    # find all parent lists that have sublists
    parents = {}
    for r in cur.execute("SELECT id, parent_list_id, priority, completed FROM liststate WHERE parent_list_id IS NOT NULL"):
        sid = r[0]; pid = r[1]; pr = r[2]; comp = r[3]
        parents.setdefault(pid, []).append({'id': sid, 'priority': pr, 'completed': bool(comp)})
    out = {}
    for pid, subs in parents.items():
        # for each sublist, compute its override per rule 3: highest uncompleted todo priority in that sublist OR immediate sublists' list-level priority
        sub_ids = [s['id'] for s in subs]
        # fetch per-sublist uncompleted todo priorities
        q = cur.execute(f"SELECT id, list_id, priority FROM todo WHERE list_id IN ({','.join(['?']*len(sub_ids))}) AND priority IS NOT NULL", sub_ids)
        todo_rows = list(q)
        todo_map = {}
        todo_ids = []
        for tid, lid, pri in todo_rows:
            todo_map.setdefault(lid, []).append((tid, pri))
            todo_ids.append(tid)
        # completed ids via todocompletion
        completed = set()
        for r in cur.execute("SELECT todo_id FROM todocompletion WHERE done=1"):
            completed.add(r[0])
        # build sublist override
        sub_overrides = {}
        # Also gather immediate sublists-of-sublist priorities
        # find sub-sub lists
        subsub_map = {}
        for r in cur.execute("SELECT id, parent_list_id, priority, completed FROM liststate WHERE parent_list_id IN ({})".format(','.join(['?']*len(sub_ids))), sub_ids):
            sid, parent_id, spri, scomp = r
            subsub_map.setdefault(parent_id, []).append({'id': sid, 'priority': spri, 'completed': bool(scomp)})
        for s in subs:
            lid = s['id']
            maxp = None
            for tid, pri in todo_map.get(lid, []):
                if tid in completed:
                    continue
                if pri is None:
                    continue
                try:
                    pv = int(pri)
                except Exception:
                    continue
                if maxp is None or pv > maxp:
                    maxp = pv
            # include priorities of immediate sublists-of-this-sublist
            for ss in subsub_map.get(lid, []):
                sp = ss.get('priority')
                scomp = ss.get('completed')
                if sp is None:
                    continue
                if scomp:
                    continue
                try:
                    spv = int(sp)
                except Exception:
                    continue
                if maxp is None or spv > maxp:
                    maxp = spv
            sub_overrides[lid] = maxp
        out[pid] = {'sublists': subs, 'overrides': sub_overrides}
    return out


print('\nParent lists with computed sublist overrides:')
res = compute_sublists_override_for_parents()
for pid, info in res.items():
    print('Parent', pid)
    for s in info.get('sublists', []):
        so = info.get('overrides', {}).get(s['id'])
        print('  sublist', s['id'], 'list-priority=', s.get('priority'), 'computed_override=', so)

con.close()
