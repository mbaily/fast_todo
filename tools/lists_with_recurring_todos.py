"""Find lists that contain todos with recurrence rules."""
from app.db import async_session
from app.models import ListState, Todo
from sqlmodel import select
import asyncio

async def _run():
    async with async_session() as sess:
        q = await sess.exec(select(ListState))
        lists = q.all()
        out = []
        for l in lists:
            q2 = await sess.exec(select(Todo).where(Todo.list_id == l.id).where(Todo.recurrence_rrule != None))
            todos = q2.all()
            if todos:
                out.append((l, todos))
        print('lists containing recurring todos:', len(out))
        for l, todos in out:
            print('list id', l.id, 'name', l.name, 'recurring_todos_count', len(todos))
            for t in todos:
                print('  todo id', t.id, 'text', t.text, 'rrule', t.recurrence_rrule)

if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(_run())
