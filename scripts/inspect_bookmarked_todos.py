import asyncio
import argparse
import os
import sys
from typing import Optional, List, Tuple

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.db import async_session
from app.models import ListState, Todo
from sqlalchemy import select

async def main(todo_ids: List[int]):
    async with async_session() as sess:
        for tid in todo_ids:
            t = await sess.get(Todo, tid)
            if not t:
                print(f"Todo {tid}: NOT FOUND")
                continue
            owner_id = None
            # fetch owning list to infer visibility and owner
            lst = await sess.get(ListState, t.list_id)
            if lst:
                owner_id = getattr(lst, 'owner_id', None)
            print(f"Todo {tid}: text={getattr(t,'text',None)!r} list_id={t.list_id} bookmarked={bool(getattr(t,'bookmarked',False))} owner_id={owner_id}")

            # Build visible list ids (owned or public) for that owner, including sublists
            vis_q = select(ListState.id).where((ListState.owner_id == owner_id) | (ListState.owner_id == None))
            vis_ids = [rid for (rid,) in (await sess.exec(vis_q)).all()]
            print(f"  visible list ids count={len(vis_ids)} includes list? {t.list_id in set(vis_ids)}")

            # Would it appear in index bookmarked todos?
            appears = bool(getattr(t, 'bookmarked', False) and (t.list_id in set(vis_ids)))
            print(f"  index selection would include: {appears}")

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Check if todos would be selected for Bookmarks section')
    p.add_argument('todo_ids', nargs='+', type=int)
    args = p.parse_args()
    asyncio.run(main(args.todo_ids))
