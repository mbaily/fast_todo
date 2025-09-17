#!/usr/bin/env python3
"""
Populate calendar-related persisted data for all or selected todos:
- plain_dates_meta: snapshot of explicit/plain date matches from text+note
- recurrence_rrule/recurrence_dtstart: inline recurrence parse result (optional)

By default runs in dry-run mode (no writes). Use --write to persist changes.

Examples:
  # Dry run for all todos
  python scripts/populate_calendar_cache.py

  # Populate just id 930 and write changes
  python scripts/populate_calendar_cache.py --ids 930 --write

  # Limit to first 500 todos, commit every 100
  python scripts/populate_calendar_cache.py --limit 500 --commit-every 100 --write

Notes:
- This script uses the app's async DB session and utility parsers.
- It does NOT honor DISABLE_CALENDAR_TEXT_SCAN (intentionally); it always scans
  text to compute snapshots. Use --skip-plain or --skip-recurring to control behavior.
"""
from __future__ import annotations
import argparse
import asyncio
import json
from typing import Iterable, Optional

# Ensure app package is importable when run from repo root
import os
import sys

# Ensure 'app' package is importable when running from repo root
_here = os.path.dirname(__file__)
_root = os.path.abspath(os.path.join(_here, os.pardir))
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.db import async_session
from app.models import Todo
from app.utils import extract_dates_meta


async def _parse_inline_recurrence(text: str) -> tuple[Optional[str], Optional[object]]:
    """Attempt to parse an inline recurrence and return (rrule_str, dtstart).
    Returns (None, None) on failure or no recurrence.
    """
    try:
        from app.utils import parse_text_to_rrule, parse_text_to_rrule_string
        r_obj, dtstart = parse_text_to_rrule(text)
        if r_obj is None or dtstart is None:
            return None, None
        _dt, rrule_str = parse_text_to_rrule_string(text)
        return rrule_str, dtstart
    except Exception:
        return None, None


def _build_plain_dates_json(meta: list[dict]) -> str:
    def _j(m: dict) -> dict:
        dd = m.get('dt')
        return {
            'year_explicit': bool(m.get('year_explicit')),
            'match_text': m.get('match_text'),
            'month': m.get('month'),
            'day': m.get('day'),
            'dt': (dd.isoformat() if hasattr(dd, 'isoformat') else dd),
        }
    return json.dumps([_j(m) for m in (meta or [])])


async def _iter_todos(ids: Optional[Iterable[int]] = None, limit: Optional[int] = None):
    async with async_session() as sess:
        from sqlmodel import select
        stmt = select(Todo)
        if ids:
            # convert to list of ints to use .in_ clause
            id_list = [int(i) for i in ids]
            stmt = stmt.where(Todo.id.in_(id_list))
        if limit:
            stmt = stmt.limit(int(limit))
        res = await sess.exec(stmt)
        for t in res.all():
            yield t


async def populate(ids: Optional[Iterable[int]], limit: Optional[int], commit_every: int, write: bool, skip_plain: bool, skip_recurring: bool, overwrite_recurring: bool) -> dict:
    changed_plain = changed_rec = 0
    scanned = 0
    # We perform writes in batches using the same session for efficiency
    async with async_session() as sess:
        from datetime import timezone
        from sqlmodel import select
        stmt = select(Todo)
        if ids:
            id_list = [int(i) for i in ids]
            stmt = stmt.where(Todo.id.in_(id_list))
        if limit:
            stmt = stmt.limit(int(limit))
        res = await sess.exec(stmt)
        todos = res.all()
        for t in todos:
            scanned += 1
            text = (t.text or '')
            note = (t.note or '')
            combined = text + ('\n' + note if note else '')
            # Plain dates
            if not skip_plain:
                try:
                    meta = extract_dates_meta(combined)
                except Exception:
                    meta = []
                new_json = _build_plain_dates_json(meta)
                if (t.plain_dates_meta or '') != (new_json or ''):
                    changed_plain += 1
                    t.plain_dates_meta = new_json
                    if write:
                        sess.add(t)
            # Inline recurrence
            if not skip_recurring:
                do_overwrite = overwrite_recurring or not getattr(t, 'recurrence_rrule', None)
                if do_overwrite:
                    rrule_str, dtstart = await _parse_inline_recurrence(combined)
                    if rrule_str and dtstart:
                        # normalize dtstart to UTC-aware
                        try:
                            if dtstart.tzinfo is None:
                                from datetime import timezone as _tz
                                dtstart = dtstart.replace(tzinfo=_tz.utc)
                            else:
                                dtstart = dtstart.astimezone(timezone.utc)
                        except Exception:
                            pass
                        if (getattr(t, 'recurrence_rrule', None) != rrule_str) or (getattr(t, 'recurrence_dtstart', None) != dtstart):
                            changed_rec += 1
                            t.recurrence_rrule = rrule_str
                            t.recurrence_dtstart = dtstart
                            if write:
                                sess.add(t)
            # Commit in batches
            if write and (scanned % max(1, commit_every) == 0):
                try:
                    await sess.commit()
                except Exception:
                    await sess.rollback()
        if write:
            try:
                await sess.commit()
            except Exception:
                await sess.rollback()
    return {'scanned': scanned, 'plain_updated': changed_plain, 'recurring_updated': changed_rec, 'written': bool(write)}


def parse_args():
    p = argparse.ArgumentParser(description="Populate calendar data for todos")
    p.add_argument('--ids', nargs='*', type=int, help='Specific todo ids to process')
    p.add_argument('--limit', type=int, help='Limit number of todos to process')
    p.add_argument('--commit-every', type=int, default=200, help='Commit every N items when --write is set')
    p.add_argument('--write', action='store_true', help='Persist changes (default is dry-run)')
    p.add_argument('--skip-plain', action='store_true', help='Skip computing plain_dates_meta')
    p.add_argument('--skip-recurring', action='store_true', help='Skip inline recurrence parsing')
    p.add_argument('--overwrite-recurring', action='store_true', help='Overwrite recurrence fields even if already set')
    return p.parse_args()


def main():
    args = parse_args()
    res = asyncio.run(populate(ids=args.ids, limit=args.limit, commit_every=args.commit_every, write=args.write,
                               skip_plain=args.skip_plain, skip_recurring=args.skip_recurring, overwrite_recurring=args.overwrite_recurring))
    print(json.dumps(res, indent=2))


if __name__ == '__main__':
    main()
