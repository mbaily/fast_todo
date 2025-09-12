"""Backfill recurrence metadata for existing todos.

Scans all Todo rows in batches and computes recurrence metadata (rrule string,
parsed meta JSON, dtstart, parser version) and updates rows in-place. This is a
simple, idempotent script intended for admin use during deployments.

Usage: run via `python tools/backfill_recurrence.py` from the project root while
the virtualenv is active. It uses the application's DB URL and SQLModel engine
so it will respect the same DB configuration used by the server.
"""
from sqlmodel import select
from app.db import async_session, init_db
from app.models import Todo
import asyncio
import json
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

BATCH = 500

async def process_batch(offset_id: int | None):
    async with async_session() as sess:
        q = select(Todo).where(Todo.id > (offset_id or 0)).order_by(Todo.id.asc()).limit(BATCH)
        res = await sess.exec(q)
        rows = res.all()
        if not rows:
            return None
        max_id = 0
        for t in rows:
            try:
                from app.utils import parse_text_to_rrule_string, parse_date_and_recurrence
                combined = (t.text or '') + '\n' + (t.note or '')
                dtstart_val, rrule_str = parse_text_to_rrule_string(combined)
                _, recdict = parse_date_and_recurrence(combined)
                t.recurrence_rrule = rrule_str or None
                t.recurrence_meta = json.dumps(recdict) if recdict else None
                t.recurrence_dtstart = dtstart_val
                t.recurrence_parser_version = 'heuristic-v1'
                sess.add(t)
                max_id = int(t.id)
            except Exception:
                logger.exception('failed to parse recurrence for todo id %s', t.id)
        try:
            await sess.commit()
        except Exception:
            await sess.rollback()
            logger.exception('failed to commit batch ending at id %s', max_id)
        return max_id

async def main():
    await init_db()
    offset = 0
    while True:
        nxt = await process_batch(offset)
        if not nxt:
            break
        offset = nxt
        logger.info('processed up to id %s', offset)
    logger.info('backfill complete')

if __name__ == '__main__':
    asyncio.run(main())
