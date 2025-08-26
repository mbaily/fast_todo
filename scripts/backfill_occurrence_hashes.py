#!/usr/bin/env python3
"""
Compute deterministic occurrence hashes for todos in a date window.
Dry-run by default (no DB writes). Use --commit to actually insert rows (not implemented here;
this script currently only reports and can be extended to write into a table).

Usage:
  PYTHONPATH=. .venv/bin/python scripts/backfill_occurrence_hashes.py --db sqlite+aiosqlite:///./test.db.server_copy --user mbaily --start 2025-07-01 --end 2025-09-30 --output /tmp/backfill_dryrun.json
"""
import argparse
import asyncio
import json
import os
from hashlib import sha256
from datetime import datetime, timezone


def _norm_iso(dt):
    if dt is None:
        return ''
    if isinstance(dt, str):
        s = dt.replace('Z', '+00:00')
        try:
            d = datetime.fromisoformat(s)
        except Exception:
            return dt.strip()
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    return str(dt)


def _canonical_json(obj):
    import json
    return json.dumps(obj, separators=(',', ':'), sort_keys=True, ensure_ascii=False)


def _sha256_hex(s):
    return sha256(s.encode('utf-8')).hexdigest()


def occurrence_hash(item_type, item_id, occurrence_dt, rrule=None, title=None):
    payload = {
        'type': str(item_type),
        'id': str(item_id),
        'dt': _norm_iso(occurrence_dt),
        'rrule': rrule or '',
        'title': (title or '').strip().lower()
    }
    cj = _canonical_json(payload)
    return 'occ:' + _sha256_hex(cj)


async def run(db_url, user_filter, start_date, end_date, out_path, commit=False, list_name=None, only_with_occ=False, top_n=None):
    # set env first so app.db picks it up
    os.environ['DATABASE_URL'] = db_url
    from sqlmodel import select
    from app.db import async_session, init_db
    from app.models import User, ListState, Todo
    # import parser helper
    from app.utils import parse_text_to_rrule_string
    from dateutil import rrule as _rrule

    await init_db()
    win_start = datetime.fromisoformat(start_date + 'T00:00:00+00:00')
    win_end = datetime.fromisoformat(end_date + 'T23:59:59+00:00')

    report = {'window_start': start_date, 'window_end': end_date, 'todos': []}
    async with async_session() as sess:
        # build base query for todos; optionally filter by user
        user = None
        if user_filter:
            q = await sess.exec(select(User).where(User.username == user_filter))
            user = q.first()
            if not user:
                print('user not found:', user_filter)
                return
        # find lists for user if provided
        list_ids = None
        if user:
            if list_name:
                q = await sess.exec(select(ListState).where(ListState.owner_id == user.id).where(ListState.name == list_name))
                l = q.first()
                if not l:
                    print('list not found for user:', list_name)
                    return
                list_ids = [l.id]
            else:
                q = await sess.exec(select(ListState).where(ListState.owner_id == user.id))
                lists = q.all()
                list_ids = [l.id for l in lists]

        if list_ids is not None:
            q = await sess.exec(select(Todo).where(Todo.list_id.in_(list_ids)))
        else:
            q = await sess.exec(select(Todo))
        todos = q.all()

        total_occ = 0
        for t in todos:
            text = t.text
            title = t.text
            try:
                dtstart, rrule_text = parse_text_to_rrule_string(text)
            except Exception as e:
                dtstart, rrule_text = None, None
            occs = []
            # if rrule_text present, expand
            if rrule_text:
                try:
                    # rrulestr expects 'RRULE:' prefix or bare; pass dtstart to rrulestr
                    from dateutil.rrule import rrulestr
                    rule = rrulestr('RRULE:' + rrule_text, dtstart=dtstart)
                    between = list(rule.between(win_start, win_end, inc=True))
                    occs = between
                except Exception:
                    occs = []
            else:
                # if dtstart exists and in window, include it
                if dtstart is not None:
                    if win_start <= dtstart <= win_end:
                        occs = [dtstart]

            hlist = []
            for o in occs:
                h = occurrence_hash('todo', t.id, o, rrule_text, title)
                hlist.append({'occurrence_dt': _norm_iso(o), 'hash': h})
            total_occ += len(hlist)
            report['todos'].append({'todo_id': t.id, 'text': text, 'count': len(hlist), 'occurrences': hlist})

        report['summary'] = {'todos_processed': len(todos), 'total_occurrences': total_occ}

    # optionally filter report for only todos with occurrences and top N
    if only_with_occ:
        filtered = [t for t in report['todos'] if t.get('count', 0) > 0]
    else:
        filtered = report['todos']
    if top_n:
        filtered = sorted(filtered, key=lambda x: x.get('count', 0), reverse=True)[:top_n]
    report_out = dict(report)
    report_out['todos'] = filtered

    # write report
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(report_out, f, indent=2, default=str)
    print('wrote', out_path)
    # commit behavior not implemented in this script; it's a dry-run reporter


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', required=True, help='SQLAlchemy DATABASE_URL to use')
    p.add_argument('--user', help='username to filter todos')
    p.add_argument('--list-name', help='optional list name to restrict todos')
    p.add_argument('--start', required=True, help='start date YYYY-MM-DD')
    p.add_argument('--end', required=True, help='end date YYYY-MM-DD')
    p.add_argument('--output', default='/tmp/backfill_report.json')
    p.add_argument('--commit', action='store_true', help='actually write back (not implemented)')
    p.add_argument('--only-with-occurrences', action='store_true', help='only include todos that had occurrences in the report')
    p.add_argument('--top', type=int, help='limit output to top N todos by occurrence count')
    args = p.parse_args()
    asyncio.run(run(args.db, args.user, args.start, args.end, args.output, commit=args.commit, list_name=args.list_name, only_with_occ=args.only_with_occurrences, top_n=args.top))


if __name__ == '__main__':
    main()
