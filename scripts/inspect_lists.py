import asyncio
import argparse
import os
import sys
from typing import Optional, List

# Ensure repo root is on sys.path
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.db import async_session
from app.models import ListState
from sqlalchemy import or_, select


async def get_list(sess, lid: int) -> Optional[ListState]:
    try:
        return await sess.get(ListState, lid)
    except Exception:
        return None


async def lineage(sess, lid: Optional[int]) -> List[int]:
    chain: List[int] = []
    cur = lid
    seen = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        lst = await get_list(sess, cur)
        if not lst:
            break
        chain.append(int(cur))
        try:
            cur = int(lst.parent_list_id) if lst.parent_list_id is not None else None
        except Exception:
            cur = None
    return chain


async def main(ids: List[int]):
    async with async_session() as sess:
        # find Trash id for the owner of the first list (if any)
        trash_ids_cache = {}

        for lid in ids:
            lst = await get_list(sess, lid)
            if not lst:
                print(f"List {lid}: NOT FOUND")
                continue
            owner_id = getattr(lst, 'owner_id', None)
            parent_list_id = getattr(lst, 'parent_list_id', None)
            parent_todo_id = getattr(lst, 'parent_todo_id', None)
            name = getattr(lst, 'name', None)
            bookmarked = bool(getattr(lst, 'bookmarked', False))
            print(f"List {lid}: name={name!r} owner_id={owner_id} bookmarked={bookmarked} parent_list_id={parent_list_id} parent_todo_id={parent_todo_id}")

            # compute lineage and check if under Trash
            chain = await lineage(sess, parent_list_id)
            print(f"  lineage up (parent->...): {chain}")

            # find Trash for this owner
            if owner_id not in trash_ids_cache:
                try:
                    from sqlalchemy import select
                    trq = await sess.scalars(select(ListState.id).where(ListState.owner_id == owner_id).where(ListState.name == 'Trash'))
                    trash_ids_cache[owner_id] = trq.first()
                except Exception:
                    trash_ids_cache[owner_id] = None
            trash_id = trash_ids_cache.get(owner_id)
            in_trash_direct = (parent_list_id == trash_id) if trash_id is not None else False
            in_trash_any = (trash_id in chain) if (trash_id is not None and chain) else False
            print(f"  trash_id={trash_id} direct_child_of_trash={in_trash_direct} under_trash_any_depth={in_trash_any}")

        # Emulate index selection of bookmarked lists for each distinct owner in input ids
        owners = sorted({getattr(await get_list(sess, lid), 'owner_id', None) for lid in ids if await get_list(sess, lid)})
        for owner_id in owners:
            if owner_id is None:
                continue
            if owner_id not in trash_ids_cache:
                try:
                    trq = await sess.scalars(select(ListState.id).where(ListState.owner_id == owner_id).where(ListState.name == 'Trash'))
                    trash_ids_cache[owner_id] = trq.first()
                except Exception:
                    trash_ids_cache[owner_id] = None
            trash_id = trash_ids_cache.get(owner_id)
            qbl = select(ListState).where(ListState.owner_id == owner_id).where(ListState.bookmarked == True)
            if trash_id is not None:
                # Include top-level lists (NULL parent) and any list whose parent is not Trash
                qbl = qbl.where(or_(ListState.parent_list_id == None, ListState.parent_list_id != trash_id))
            qbl = qbl.order_by(ListState.modified_at.desc())
            res = await sess.exec(qbl)
            rows = res.all()
            ids = []
            for r in rows:
                obj = r
                # Handle SQLAlchemy Row objects
                try:
                    if hasattr(r, '_mapping') and r._mapping:
                        # take the first value from the mapping (expected ListState)
                        obj = next(iter(r._mapping.values()))
                except Exception:
                    pass
                # If it's a ListState instance, use its id; otherwise try to coerce
                try:
                    if isinstance(obj, ListState):
                        ids.append(int(obj.id))
                    else:
                        ids.append(int(getattr(obj, 'id', obj)))
                except Exception:
                    # ignore uncoercible entries
                    pass
            print(f"\nIndex-style bookmarked lists for owner {owner_id} (trash_id={trash_id}): {ids}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Inspect ListState rows by id.')
    parser.add_argument('ids', nargs='+', type=int, help='List IDs to inspect')
    args = parser.parse_args()
    asyncio.run(main(args.ids))
