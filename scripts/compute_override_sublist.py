"""
Compute the dedicated-style override for a single sublist id.
Usage: python3 scripts/compute_override_sublist.py <sublist_id>
"""
import sys
import sqlite3

DB = 'fast_todo.db'


def get_default_completion_type_id(conn):
    cur = conn.cursor()
    cur.execute("SELECT id FROM completiontype WHERE name = 'default' ORDER BY id LIMIT 1")
    r = cur.fetchone()
    return r[0] if r else None


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python3 scripts/compute_override_sublist.py <sublist_id>')
        sys.exit(2)
    sid = int(sys.argv[1])
    conn = sqlite3.connect(DB)
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
    print('Dedicated-style override for sublist', sid, '=>', max_p)
    conn.close()
