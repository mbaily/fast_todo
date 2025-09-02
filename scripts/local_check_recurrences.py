#!/usr/bin/env python3
"""
Compute calendar occurrences directly against a DB file (fast_todo.db.server_copy) for dev_user
and check seeded recurrence phrases across specified months.

Usage: python3 scripts/local_check_recurrences.py --db fast_todo.db.server_copy --months 2025-08,2025-09,2025-10 --out report.json
"""
import asyncio, os, json
from datetime import datetime, timezone
from sqlmodel import select

# ensure repo root in PYTHONPATH
ROOT = os.path.dirname(os.path.dirname(__file__))
os.environ['PYTHONPATH'] = os.environ.get('PYTHONPATH','') + ':' + ROOT

from app.db import async_session, init_db
from app.models import User, ListState, Todo
from app.utils import parse_text_to_rrule, parse_text_to_rrule_string, extract_dates_meta, now_utc
from dateutil.rrule import rrulestr, rrule

async def compute_occurrences_for_window(owner_username, start_dt, end_dt, max_per_item=100):
    async with async_session() as sess:
        # find user
        res = await sess.exec(select(User).where(User.username == owner_username))
        user = res.first()
        if not user:
            raise SystemExit('user not found')
        owner_id = user.id
        # lists
        ql = await sess.exec(select(ListState).where(ListState.owner_id == owner_id))
        lists = ql.all()
        list_ids = [l.id for l in lists if l.id is not None]
        todos = []
        if list_ids:
            qt = await sess.exec(select(Todo).where(Todo.list_id.in_(list_ids)))
            todos = qt.all()

        occurrences = []
        def add_occ(item_type, item_id, list_id, title, occ_dt, dtstart, is_rec, rrule_str, rec_meta):
            occurrences.append({
                'occurrence_dt': occ_dt.isoformat(),
                'item_type': item_type,
                'id': item_id,
                'list_id': list_id,
                'title': title,
                'dtstart': dtstart.isoformat() if dtstart else None,
                'is_recurring': bool(is_rec),
                'rrule': rrule_str or '',
            })

        # process todos
        for t in todos:
            combined = ' \n '.join([t.text or '', t.note or ''])
            rec_rrule = getattr(t, 'recurrence_rrule', None)
            rec_dtstart = getattr(t, 'recurrence_dtstart', None)
            if rec_rrule:
                try:
                    dtstart = rec_dtstart
                    if dtstart and dtstart.tzinfo is None:
                        dtstart = dtstart.replace(tzinfo=timezone.utc)
                    r = rrulestr(rec_rrule, dtstart=dtstart)
                    occs = list(r.between(start_dt, end_dt, inc=True))[:max_per_item]
                    for od in occs:
                        add_occ('todo', t.id, t.list_id, t.text, od, dtstart, True, rec_rrule, None)
                    continue
                except Exception:
                    pass
            # inline parse
            try:
                r_obj, dtstart = parse_text_to_rrule(combined)
                if r_obj is not None and dtstart is not None:
                    if dtstart.tzinfo is None:
                        dtstart = dtstart.replace(tzinfo=timezone.utc)
                    _dt, rrule_str_local = parse_text_to_rrule_string(combined)
                    occs = list(r_obj.between(start_dt, end_dt, inc=True))[:max_per_item]
                    for od in occs:
                        add_occ('todo', t.id, t.list_id, t.text, od, dtstart, True, rrule_str_local, None)
                    continue
            except Exception:
                pass
            # fallback: explicit dates
            meta = extract_dates_meta(combined)
            explicit = [m for m in meta if m.get('year_explicit')]
            for m in explicit:
                d = m.get('dt')
                if d >= start_dt and d <= end_dt:
                    add_occ('todo', t.id, t.list_id, t.text, d, None, False, '', None)
            # yearless
            yearless = [m for m in meta if not m.get('year_explicit')]
            if yearless:
                ys = range(start_dt.year, end_dt.year + 1)
                for m in yearless:
                    mon = int(m.get('month'))
                    day = int(m.get('day'))
                    for y in ys:
                        try:
                            od = datetime(y, mon, day, tzinfo=timezone.utc)
                        except Exception:
                            continue
                        if od >= start_dt and od <= end_dt:
                            add_occ('todo', t.id, t.list_id, t.text, od, None, False, '', None)
        # sort
        occurrences.sort(key=lambda x: x.get('occurrence_dt'))
        return occurrences

async def main(db, months_csv, out_path):
    if db:
        os.environ['DATABASE_URL'] = f"sqlite+aiosqlite:///{os.path.abspath(db)}"
    await init_db()
    months = months_csv.split(',')
    # load phrases
    phrases_file = os.path.join(ROOT, 'tests', 'recurrence_phrases.json')
    phrases = [ (x if isinstance(x, str) else x.get('text')) for x in json.load(open(phrases_file, 'r', encoding='utf-8')) ]
    report = {}
    for p in phrases:
        report[p] = {}
    for m in months:
        y, mm = map(int, m.split('-'))
        start = datetime(y, mm, 1, tzinfo=timezone.utc)
        import calendar
        last = calendar.monthrange(y, mm)[1]
        end = datetime(y, mm, last, 23,59,59, tzinfo=timezone.utc)
        occs = await compute_occurrences_for_window('dev_user', start, end)
        # count per phrase
        for p in phrases:
            low = (p or '').lower()
            c = sum(1 for o in occs if low in (o.get('title') or '').lower())
            report[p][m] = c
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    print('Wrote', out_path)

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--db', default='fast_todo.db.server_copy')
    p.add_argument('--months', default='2025-08,2025-09,2025-10')
    p.add_argument('--out', default='scripts/local_recurrence_report.json')
    args = p.parse_args()
    asyncio.run(main(args.db, args.months, args.out))
