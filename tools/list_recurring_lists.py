"""List lists that have recurrence_rrule set."""
from app.db import async_session
from app.models import ListState
from sqlmodel import select
import asyncio

async def _run():
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.recurrence_rrule != None))
        rows = q.all()
        print('lists with recurrence:', len(rows))
        for r in rows:
            print('id:', r.id, 'name:', r.name, 'rrule:', r.recurrence_rrule, 'meta:', r.recurrence_meta)

if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(_run())
