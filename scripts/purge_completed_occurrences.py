#!/usr/bin/env python3
"""Purge CompletedOccurrence rows from the DB for testing/dev.

Usage:
  python scripts/purge_completed_occurrences.py --dry-run
  python scripts/purge_completed_occurrences.py --yes
  python scripts/purge_completed_occurrences.py --user 1 --before 2025-01-01T00:00:00Z --yes

Defaults to a dry-run: you must pass --yes to actually delete rows.
"""
import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path when running the script directly
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlmodel import select

from app.db import async_session
from app.models import CompletedOccurrence


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        ss = s.replace('Z', '+00:00')
        d = datetime.fromisoformat(ss)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


async def main(args):
    where = []
    params = {}
    if args.user is not None:
        where.append(CompletedOccurrence.user_id == int(args.user))
    if args.before:
        dt = _parse_iso(args.before)
        if dt is None:
            print('Invalid --before datetime; use ISO format like 2025-01-01T00:00:00Z')
            return 2
        where.append(CompletedOccurrence.occurrence_dt <= dt)

    async with async_session() as sess:
        if where:
            q = select(CompletedOccurrence).where(*where)
        else:
            q = select(CompletedOccurrence)
        res = await sess.exec(q)
        rows = res.all()
        print(f'Found {len(rows)} CompletedOccurrence rows matching criteria')
        if rows:
            print('Sample rows:')
            for r in rows[:10]:
                print(f'  id={getattr(r, "id", None)} user_id={r.user_id} item_type={r.item_type} item_id={r.item_id} occurrence_dt={r.occurrence_dt}')
        if args.dry_run:
            print('Dry-run mode; no rows will be deleted.')
            return 0
        if not args.yes:
            print('\nTo actually delete these rows re-run with --yes')
            return 0
        # perform delete in a single statement for efficiency
        from sqlalchemy import delete as sqlalchemy_delete

        if where:
            del_q = sqlalchemy_delete(CompletedOccurrence).where(*where)
        else:
            del_q = sqlalchemy_delete(CompletedOccurrence)
        res2 = await sess.exec(del_q)
        try:
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise
        print(f'Deleted {res2.rowcount if hasattr(res2, "rowcount") else "?"} rows')
        return 0


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Purge CompletedOccurrence rows (dev/testing)')
    p.add_argument('--dry-run', action='store_true', help='List matching rows but do not delete')
    p.add_argument('--yes', action='store_true', help='Actually perform deletion')
    p.add_argument('--user', type=int, help='Restrict to specific user_id')
    p.add_argument('--before', type=str, help='Only delete occurrences on or before this ISO datetime (e.g. 2025-01-01T00:00:00Z)')
    args = p.parse_args()
    try:
        raise SystemExit(asyncio.run(main(args)))
    except KeyboardInterrupt:
        print('\nAborted by user')
        raise
