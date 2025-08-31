"""Diagnostic script: compute override priorities for sublists of a given todo id using the same logic as app/main.html_view_todo.

Usage: run inside project venv: python scripts/diagnose_override.py <todo_id>
"""
import sys
from sqlmodel import select
from app.db import async_session
from app.models import ListState, Todo, TodoCompletion, CompletionType
import asyncio

async def run(todo_id:int):
    async with async_session() as sess:
        qsubs = await sess.exec(select(ListState).where(ListState.parent_todo_id==todo_id))
        rows = qsubs.all()
        sub_ids = [r.id for r in rows if r.id is not None]
        print('sublists:', [(r.id, r.name, r.priority) for r in rows])
        if not sub_ids:
            print('no sublists')
            return
        q = await sess.exec(select(Todo.id, Todo.list_id, Todo.priority).where(Todo.list_id.in_(sub_ids)).where(Todo.priority != None))
        todo_id_rows = q.all()
        print('todo_id_rows:', todo_id_rows)
        todo_map = {}
        todo_ids = []
        for tid, lid, pri in todo_id_rows:
            todo_map.setdefault(lid, []).append((tid, pri))
            todo_ids.append(tid)
        completed_ids = set()
        if todo_ids:
            try:
                qcomp = await sess.exec(select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(todo_ids)).where(CompletionType.name=='default').where(TodoCompletion.done==True))
                cres = qcomp.all()
                completed_ids = set(r[0] if isinstance(r, tuple) else r for r in cres)
            except Exception as e:
                print('comp query failed', e)
        print('completed_ids:', completed_ids)
        for sid in sub_ids:
            candidates = todo_map.get(sid, [])
            max_p = None
            for tid, pri in candidates:
                if tid in completed_ids:
                    continue
                if pri is None:
                    continue
                try:
                    pv = int(pri)
                except Exception:
                    continue
                if max_p is None or pv > max_p:
                    max_p = pv
            print('sublist', sid, 'override', max_p)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('usage: python scripts/diagnose_override.py <todo_id>')
        sys.exit(2)
    todo_id = int(sys.argv[1])
    asyncio.run(run(todo_id))
