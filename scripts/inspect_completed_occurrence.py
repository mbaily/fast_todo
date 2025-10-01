#!/usr/bin/env python3
"""Inspect CompletedOccurrence rows by id or list null-metadata rows."""
import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import async_session, init_db
from app.models import CompletedOccurrence
from sqlmodel import select


async def main(row_id: int | None, list_nulls: bool):
    await init_db()
    async with async_session() as sess:
        if list_nulls:
            q = select(CompletedOccurrence).where(
                (CompletedOccurrence.item_type == None) |
                (CompletedOccurrence.item_id == None) |
                (CompletedOccurrence.occurrence_dt == None)
            )
            res = await sess.exec(q)
            rows = res.all()
            print(f'Found {len(rows)} rows with missing metadata')
            for r in rows:
                print(f'id={r.id} user_id={r.user_id} occ_hash={r.occ_hash!r} item_type={r.item_type!r} item_id={r.item_id!r} occurrence_dt={r.occurrence_dt!r} completed_at={r.completed_at!r}')
            return

        if row_id is None:
            print('Specify --id or --list-nulls')
            return
        r = await sess.get(CompletedOccurrence, int(row_id))
        if not r:
            print('No row with id', row_id)
            return
        print('Row:')
        print(f'  id: {r.id}')
        print(f'  user_id: {r.user_id}')
        print(f'  occ_hash: {r.occ_hash!r}')
        print(f'  item_type: {r.item_type!r}')
        print(f'  item_id: {r.item_id!r}')
        print(f'  occurrence_dt: {r.occurrence_dt!r}')
        print(f'  completed_at: {r.completed_at!r}')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--id', type=int, help='CompletedOccurrence id to inspect')
    p.add_argument('--list-nulls', action='store_true', help='List rows with null metadata')
    args = p.parse_args()
    asyncio.run(main(args.id, args.list_nulls))
