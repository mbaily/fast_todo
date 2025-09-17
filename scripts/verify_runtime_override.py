"""Verify latest SUBLETS_DUMP for a list against DB-computed overrides.

Usage:
  python3 scripts/verify_runtime_override.py --list-id 190 --log server.log
  cat server.log | python3 scripts/verify_runtime_override.py --list-id 190

Prints a compact summary and exits with code 0 if everything matches, 2 if any mismatches.
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


def find_latest_dump(lines, list_id):
    last = None
    for obj in parse_stream(lines, list_id_filter=list_id):
        last = obj
    return last


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--log', help='Path to server.log (default stdin)')
    p.add_argument('--list-id', type=int, required=True, help='List id to verify')
    p.add_argument('--json', action='store_true', help='Emit JSON lines')
    args = p.parse_args()

    dump = find_latest_dump(iter_lines_from(args.log), args.list_id)
    if not dump:
        print(f'No SUBLETS_DUMP found for list {args.list_id}', file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB)
    payload = dump.get('data')
    any_mismatch = False
    for entry in payload:
        sid = entry.get('id')
        logged = entry.get('override_priority')
        expected = compute_dedicated_override_for_sublist(conn, sid)
        if logged != expected:
            any_mismatch = True
        if args.json:
            print(json.dumps({'list': args.list_id, 'sublist': sid, 'logged': logged, 'expected': expected}))
        else:
            status = 'OK' if logged == expected else 'MISMATCH'
            print(f'{status}: list={args.list_id} sublist={sid} logged={logged} expected={expected}')
    conn.close()
    if any_mismatch:
        sys.exit(2)


if __name__ == '__main__':
    main()
