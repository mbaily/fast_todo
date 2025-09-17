import sqlite3
from pprint import pprint

DB = 'fast_todo.db'
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

LIST_ID = 193
print('Inspecting todos for list', LIST_ID)
# list info
r = cur.execute('SELECT id, name, priority, completed FROM liststate WHERE id=?', (LIST_ID,)).fetchone()
print('List row:', dict(r) if r else None)

# todos with priority
rows = list(cur.execute('SELECT id, text, priority FROM todo WHERE list_id=? ORDER BY id', (LIST_ID,)))
print('\nTodos in list (id, priority, text):')
for row in rows:
    print(dict(row))

# completed todo ids (using completiontype default)
completed = set()
try:
    for r in cur.execute("SELECT todocompletion.todo_id FROM todocompletion JOIN completiontype ON completiontype.id = todocompletion.completion_type_id WHERE completiontype.name='default' AND todocompletion.done=1"):
        completed.add(r[0])
except Exception as e:
    pass
print('\nCompleted todo ids (default):', sorted(completed))

# Show status per todo
print('\nTodo status:')
for row in rows:
    tid = row['id']
    print(tid, 'priority=', row['priority'], 'completed=', (tid in completed))

con.close()
