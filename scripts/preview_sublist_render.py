import sqlite3
from jinja2 import Environment, FileSystemLoader, select_autoescape

DB='fast_todo.db'
PARENT=190

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

# fetch sublists of PARENT
subs = [dict(r) for r in cur.execute('SELECT id,name,priority,completed,created_at,modified_at FROM liststate WHERE parent_list_id=?', (PARENT,)).fetchall()]
# compute override for each sublist: highest uncompleted todo priority in that sublist OR list-level priorities of sub-sub-lists
# get todos with priority in these sublists
sub_ids = [s['id'] for s in subs]
if sub_ids:
    q = 'SELECT id, list_id, priority FROM todo WHERE list_id IN ({})'.format(','.join(['?']*len(sub_ids)))
    rows = [dict(r) for r in cur.execute(q, sub_ids).fetchall()]
else:
    rows = []
# completed set
completed = set(r[0] for r in cur.execute("SELECT todo_id FROM todocompletion WHERE done=1").fetchall())
# map todos
todo_map = {}
for r in rows:
    todo_map.setdefault(r['list_id'], []).append(r)
# gather sub-sub priorities
subsub_map = {}
if sub_ids:
    q2 = 'SELECT id, parent_list_id, priority, completed FROM liststate WHERE parent_list_id IN ({})'.format(','.join(['?']*len(sub_ids)))
    for r in cur.execute(q2, sub_ids):
        sid, pid, spri, scomp = r
        subsub_map.setdefault(pid, []).append({'id': sid, 'priority': spri, 'completed': bool(scomp)})
# compute
for s in subs:
    lid = s['id']
    maxp = None
    for t in todo_map.get(lid, []):
        if t['id'] in completed:
            continue
        pr = t['priority']
        if pr is None:
            continue
        try:
            pv = int(pr)
        except Exception:
            continue
        if maxp is None or pv > maxp:
            maxp = pv
    # include immediate sublists' list-level priorities
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
    s['override_priority'] = maxp

# render snippet
env = Environment(loader=FileSystemLoader('html_no_js/templates'), autoescape=select_autoescape(['html','xml']))
# reuse circ mapping and snippet from templates
snippet = """{% set circ = {1:'①',2:'②',3:'③',4:'④',5:'⑤',6:'⑥',7:'⑦',8:'⑧',9:'⑨',10:'⑩'} %}
<div>
  <div>{{ sl.name }} (id={{ sl.id }})</div>
  {% if sl.priority is not none %}
    <div>list-priority: <span class="meta priority-inline"><span class="priority-circle">{{ circ.get(sl.priority, sl.priority) }}</span></span></div>
  {% endif %}
  {% if sl.override_priority is not none and (sl.priority is none or sl.override_priority >= sl.priority) %}
    <div>override: <span class="muted priority-inline priority-override"><span class="priority-circle">{{ circ.get(sl.override_priority, sl.override_priority) }}</span></span></div>
  {% else %}
    <div>override: (none)</div>
  {% endif %}
</div>
"""

t = env.from_string(snippet)
for s in subs:
    print(t.render(sl=s))

con.close()
