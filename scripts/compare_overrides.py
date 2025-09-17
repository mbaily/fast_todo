"""Compare index-style vs dedicated-list-style override computations.

Usage:
  python3 scripts/compare_overrides.py <parent_list_id> [--verbose]

The script will print the index-style override for the given parent list
and the dedicated-list-style override for each immediate sublist.
"""

import argparse
import sqlite3
from typing import Dict, Optional

DB = 'fast_todo.db'


def get_default_completion_type_id(conn: sqlite3.Connection) -> Optional[int]:
    cur = conn.cursor()
    cur.execute("SELECT id FROM completiontype WHERE name = 'default' ORDER BY id LIMIT 1")
    r = cur.fetchone()
    return r[0] if r else None


def index_style(conn: sqlite3.Connection, parent_list_id: int):
    cur = conn.cursor()
    cur.execute('SELECT id FROM liststate WHERE parent_list_id = ?', (parent_list_id,))
    subs = [r[0] for r in cur.fetchall()]
    combined = [parent_list_id] + subs

    if not combined:
        return None

    q = 'SELECT id, list_id, priority FROM todo WHERE list_id IN ({}) AND priority IS NOT NULL'.format(','.join('?'*len(combined)))
    cur.execute(q, combined)
    todo_rows = cur.fetchall()
    todo_map = {}
    todo_ids = []
    for tid, lid, pri in todo_rows:
        todo_map.setdefault(lid, []).append((tid, pri))
        todo_ids.append(tid)

    completed = set()
    if todo_ids:
        def_id = get_default_completion_type_id(conn)
        if def_id is not None:
            q = 'SELECT todo_id FROM todocompletion WHERE completion_type_id = ? AND todo_id IN ({}) AND done = 1'.format(','.join('?'*len(todo_ids)))
            cur.execute(q, (def_id, *todo_ids))
            completed = set(r[0] for r in cur.fetchall())

    candidates = list(todo_map.get(parent_list_id, []))
    for sid in subs:
        candidates.extend(todo_map.get(sid, []))
    max_p = None
    for tid, pri in candidates:
        if tid in completed:
            continue
        if pri is None:
            continue
        try:
            pv = int(pri)
        except Exception:
            continue
        if max_p is None or pv > max_p:
            max_p = pv
    return max_p


def dedicated_style(conn: sqlite3.Connection, parent_list_id: int, dump_sql: bool = False) -> Dict[int, Optional[int]]:
    cur = conn.cursor()
    cur.execute('SELECT id, priority, completed FROM liststate WHERE parent_list_id = ?', (parent_list_id,))
    subs = cur.fetchall()
    sub_ids = [r[0] for r in subs]

    subchild_map = {}
    if sub_ids:
        q = 'SELECT id, priority, parent_list_id, completed FROM liststate WHERE parent_list_id IN ({})'.format(','.join('?'*len(sub_ids)))
        cur.execute(q, sub_ids)
        for sid, spri, pid, scomp in cur.fetchall():
            subchild_map.setdefault(pid, []).append((sid, spri, bool(scomp)))

    def_id = get_default_completion_type_id(conn)
    if dump_sql:
        print('debug: default completion_type id =', def_id)

    results: Dict[int, Optional[int]] = {}
    for sid, spriority, scompleted in subs:
        max_p = None
        if sid is None:
            results[sid] = None
            continue
        try:
            if def_id is not None:
                q = ("SELECT t.priority FROM todo t LEFT JOIN todocompletion tc ON tc.todo_id = t.id "
                     "AND tc.completion_type_id = ? WHERE t.list_id = ? AND (tc.todo_id IS NULL OR tc.done = 0) "
                     "AND t.priority IS NOT NULL ORDER BY t.priority DESC LIMIT 1")
                cur.execute(q, (def_id, sid))
            else:
                q = "SELECT t.priority FROM todo t WHERE t.list_id = ? AND t.priority IS NOT NULL ORDER BY t.priority DESC LIMIT 1"
                cur.execute(q, (sid,))
            r = cur.fetchone()
            if r:
                max_p = int(r[0])
        except Exception:
            max_p = None

        # optional debugging dump: show up to 10 candidate todos for this sublist
        if dump_sql:
            try:
                if def_id is not None:
                    q2 = ("SELECT t.id, t.priority, tc.done FROM todo t LEFT JOIN todocompletion tc ON tc.todo_id = t.id "
                          "AND tc.completion_type_id = ? WHERE t.list_id = ? ORDER BY t.priority DESC LIMIT 10")
                    cur.execute(q2, (def_id, sid))
                else:
                    q2 = "SELECT id, priority, NULL as done FROM todo WHERE list_id = ? ORDER BY priority DESC LIMIT 10"
                    cur.execute(q2, (sid,))
                rows = cur.fetchall()
                print(f"debug: sublist {sid} sample todos (id, priority, done):", rows)
            except Exception:
                print(f"debug: failed to fetch sample todos for sublist {sid}")

        for (_ssid, spri, scomp) in subchild_map.get(sid, []):
            if spri is None:
                continue
            if scomp:
                continue
            try:
                spv = int(spri)
            except Exception:
                continue
            if max_p is None or spv > max_p:
                max_p = spv
        results[sid] = max_p

    return results


def main():
    p = argparse.ArgumentParser()
    p.add_argument('parent_list_id', type=int)
    p.add_argument('--verbose', '-v', action='store_true')
    p.add_argument('--dump-sql', action='store_true', help='Dump sample SQL rows for each sublist')
    p.add_argument('--json', action='store_true', help='Emit machine-readable JSON output')
    args = p.parse_args()

    conn = sqlite3.connect(DB)
    idx = index_style(conn, args.parent_list_id)
    submap = dedicated_style(conn, args.parent_list_id, dump_sql=bool(getattr(args, 'dump_sql', False)))
    if getattr(args, 'json', False):
        import json as _json
        print(_json.dumps({'parent_list_id': args.parent_list_id, 'index_override': idx, 'sublist_overrides': submap}))
    else:
        print('Index-style override for list', args.parent_list_id, '=>', idx)
        print('Dedicated-style sublist overrides for immediate sublists of', args.parent_list_id)
        for sid, val in submap.items():
            print('  sublist', sid, '=>', val)
    conn.close()


if __name__ == '__main__':
    main()
