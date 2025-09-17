"""Check SUBLETS_DUMP entries in a server log against DB-computed overrides.

Reads from a logfile (default stdin) and for each SUBLETS_DUMP entry computes the
expected dedicated-style override for each sublist id using the local SQLite DB
(`fast_todo.db`). Prints human-readable lines or JSON when --json is specified.

Exit code:
  0 -- all ok
  2 -- one or more mismatches found

Usage:
  python3 scripts/check_sublists_consistency.py --log server.log
  cat server.log | python3 scripts/check_sublists_consistency.py
"""

import argparse
import json
import sqlite3
import sys
from typing import Optional

from parse_server_sublists import parse_stream, iter_lines_from

DB = 'fast_todo.db'


def get_default_completion_type_id(conn: sqlite3.Connection) -> Optional[int]:
    cur = conn.cursor()
    cur.execute("SELECT id FROM completiontype WHERE name = 'default' ORDER BY id LIMIT 1")
    r = cur.fetchone()
    return r[0] if r else None


def compute_dedicated_override_for_sublist(conn: sqlite3.Connection, sid: int) -> Optional[int]:
    cur = conn.cursor()
    def_id = get_default_completion_type_id(conn)
    max_p = None
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

    # include immediate child lists' own priority
    cur.execute('SELECT id, priority, completed FROM liststate WHERE parent_list_id = ?', (sid,))
    for _ssid, spri, scomp in cur.fetchall():
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
    return max_p


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--log', help='Path to server.log (default stdin)')
    p.add_argument('--list-id', type=int, help='Only check dumps for this list id')
    p.add_argument('--json', action='store_true', help='Emit JSON lines')
    args = p.parse_args()

    conn = sqlite3.connect(DB)
    any_mismatch = False

    for obj in parse_stream(iter_lines_from(args.log), list_id_filter=args.list_id):
        lid = obj.get('list')
        payload = obj.get('data')
        if payload is None:
            # skip raw/unparseable entries
            if not args.json:
                print(f'failed to parse payload for list {lid}', file=sys.stderr)
            continue
        for entry in payload:
            sid = entry.get('id')
            logged = entry.get('override_priority')
            expected = compute_dedicated_override_for_sublist(conn, sid)
            if logged != expected:
                any_mismatch = True
                if args.json:
                    print(json.dumps({'list': lid, 'sublist': sid, 'logged': logged, 'expected': expected}))
                else:
                    print(f'mismatch list={lid} sublist={sid} logged={logged} expected={expected}')
            else:
                if args.json:
                    print(json.dumps({'list': lid, 'sublist': sid, 'ok': True, 'value': logged}))
                else:
                    print(f'ok      list={lid} sublist={sid} value={logged}')

    conn.close()
    if any_mismatch:
        sys.exit(2)


if __name__ == '__main__':
    main()
