#!/usr/bin/env python3
"""
Login with username/password and fetch /calendar/occurrences for a given month/year.

Usage:
  ./scripts/fetch_occurrences.py --host 0.0.0.0 --port 10443 --username mbaily --password 'pw' 2025 8

Outputs JSON to stdout.
"""
import argparse
import asyncio
import sys
import urllib.parse

parser = argparse.ArgumentParser(description='Fetch calendar occurrences via API')
parser.add_argument('year', type=int)
parser.add_argument('month', type=int)
parser.add_argument('--months', type=int, default=1, help='number of months to include starting at year/month (default 1)')
parser.add_argument('--pattern', action='append', help='regex pattern to search for in occurrence JSON/title (repeatable)')
parser.add_argument('--ignore-case', action='store_true', help='case-insensitive regex matching')
parser.add_argument('--host', default='0.0.0.0')
parser.add_argument('--port', default=10443, type=int)
parser.add_argument('--username', required=True)
parser.add_argument('--password', required=True)
parser.add_argument('--insecure', action='store_true', help='ignore TLS cert')
parser.add_argument('--output', help='write output to file instead of stdout')
args = parser.parse_args()

if not (1 <= args.month <= 12):
    print('invalid month', file=sys.stderr); sys.exit(2)

base = f"https://{args.host}:{args.port}"
login_url = base + '/auth/token'
occ_url = base + '/calendar/occurrences'

# use requests if available
try:
    import requests
    has_requests = True
except Exception:
    has_requests = False

payload = {'username': args.username, 'password': args.password}

if has_requests:
    sess = requests.Session()
    try:
        r = sess.post(login_url, json=payload, verify=not args.insecure, timeout=10)
        r.raise_for_status()
        token = r.json().get('access_token')
    except Exception as e:
        print('login failed:', e, file=sys.stderr); sys.exit(3)
    headers = {'Authorization': f'Bearer {token}'}
    import calendar, datetime, re, json
    flags = re.IGNORECASE if args.ignore_case else 0
    compiled = [re.compile(p, flags) for p in (args.pattern or [])]
    occurrences = []
    seen = set()
    y = args.year
    m = args.month
    for i in range(args.months):
        # compute start/end for month y/m
        start = f"{y:04d}-{m:02d}-01T00:00:00Z"
        last_day = calendar.monthrange(y, m)[1]
        end = f"{y:04d}-{m:02d}-{last_day:02d}T23:59:59Z"
        params = {'start': start, 'end': end}
        try:
            r2 = sess.get(occ_url, params=params, headers=headers, verify=not args.insecure, timeout=10)
            r2.raise_for_status()
            data = r2.json()
        except Exception as e:
            print('failed to fetch occurrences:', e, file=sys.stderr); sys.exit(4)
        for occ in data.get('occurrences', []):
            key = (occ.get('item_type'), occ.get('id'), occ.get('occurrence_dt'))
            if key in seen:
                continue
            seen.add(key)
            if compiled:
                text = (occ.get('title') or '')
                # also search serialized JSON for full-field matching
                jtext = json.dumps(occ, default=str)
                if any(p.search(text) or p.search(jtext) for p in compiled):
                    occurrences.append(occ)
            else:
                occurrences.append(occ)
        # advance month
        dt = datetime.date(y, m, 15) + datetime.timedelta(days=31)
        y = dt.year; m = dt.month
    # compute overall window start/end for possible local expansion
    first_start = args.year, args.month
    # find last month after advancing months-1
    import datetime as _dt
    ly = args.year; lm = args.month
    for _i in range(max(0, args.months-1)):
        tmp = _dt.date(ly, lm, 15) + _dt.timedelta(days=31)
        ly, lm = tmp.year, tmp.month
    last_day = calendar.monthrange(ly, lm)[1]
    overall_start = f"{args.year:04d}-{args.month:02d}-01T00:00:00Z"
    overall_end = f"{ly:04d}-{lm:02d}-{last_day:02d}T23:59:59Z"
    out = {'occurrences': occurrences, 'truncated': False, 'window_start': overall_start, 'window_end': overall_end}
else:
    # fallback: use urllib and manual token retrieval
    import urllib.request, json, ssl
    ctx = None
    if args.insecure:
        ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
    req = urllib.request.Request(login_url, data=bytes(json.dumps(payload), 'utf-8'), headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            j = json.load(r)
            token = j.get('access_token')
    except Exception as e:
        print('login failed:', e, file=sys.stderr); sys.exit(3)
    # urllib fallback: iterate months similarly
    import urllib.request, json, ssl, calendar, datetime, re
    flags = re.IGNORECASE if args.ignore_case else 0
    compiled = [re.compile(p, flags) for p in (args.pattern or [])]
    occurrences = []
    seen = set()
    y = args.year
    m = args.month
    for i in range(args.months):
        start = f"{y:04d}-{m:02d}-01T00:00:00Z"
        last_day = calendar.monthrange(y, m)[1]
        end = f"{y:04d}-{m:02d}-{last_day:02d}T23:59:59Z"
        params = urllib.parse.urlencode({'start': start, 'end': end})
        occ_full = occ_url + '?' + params
        req2 = urllib.request.Request(occ_full, headers={'Authorization': f'Bearer {token}'})
        try:
            with urllib.request.urlopen(req2, context=ctx, timeout=10) as r:
                data = json.load(r)
        except Exception as e:
            print('failed to fetch occurrences:', e, file=sys.stderr); sys.exit(4)
        for occ in data.get('occurrences', []):
            key = (occ.get('item_type'), occ.get('id'), occ.get('occurrence_dt'))
            if key in seen:
                continue
            seen.add(key)
            if compiled:
                text = (occ.get('title') or '')
                jtext = json.dumps(occ, default=str)
                if any(p.search(text) or p.search(jtext) for p in compiled):
                    occurrences.append(occ)
            else:
                occurrences.append(occ)
        dt = datetime.date(y, m, 15) + datetime.timedelta(days=31)
        y = dt.year; m = dt.month
    # compute overall window start/end for possible local expansion (urllib path)
    first_start = args.year, args.month
    import datetime as _dt
    ly = args.year; lm = args.month
    for _i in range(max(0, args.months-1)):
        tmp = _dt.date(ly, lm, 15) + _dt.timedelta(days=31)
        ly, lm = tmp.year, tmp.month
    last_day = calendar.monthrange(ly, lm)[1]
    overall_start = f"{args.year:04d}-{args.month:02d}-01T00:00:00Z"
    overall_end = f"{ly:04d}-{lm:02d}-{last_day:02d}T23:59:59Z"
    out = {'occurrences': occurrences, 'truncated': False, 'window_start': overall_start, 'window_end': overall_end}

# Heuristic: if server did not expand some inline recurrences, try a local expansion
try:
    from dateutil import rrule as _rrule
    import datetime as _dt
    # parse overall window into datetimes
    def _parse_iso(s):
        from datetime import datetime, timezone
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    win_start = _parse_iso(out['window_start'])
    win_end = _parse_iso(out['window_end'])
    # build lookup of existing keys
    existing = set((o.get('item_type'), o.get('id'), o.get('occurrence_dt')) for o in out.get('occurrences', []))
    # simple recurrence regex: 'every', optional number, unit
    import re
    simple_re = re.compile(r"every\s+(?:other\b|(?P<n>\d+)\s*)?(?P<unit>day|days|week|weeks|month|months|year|years)", re.I)
    # helper to coerce unit to dateutil freq
    freq_map = {'day': _rrule.DAILY, 'days': _rrule.DAILY, 'week': _rrule.WEEKLY, 'weeks': _rrule.WEEKLY, 'month': _rrule.MONTHLY, 'months': _rrule.MONTHLY, 'year': _rrule.YEARLY, 'years': _rrule.YEARLY}
    # attempt expansion for titles that include 'every' and no rrule present
    additions = []
    for occ in list(out.get('occurrences', [])):
        title = occ.get('title') or ''
        if 'every' not in title.lower():
            continue
        # if server already marked as recurring and rrule present, skip
        if occ.get('is_recurring') and occ.get('rrule'):
            continue
        m = simple_re.search(title)
        if not m:
            continue
        unit = m.group('unit')
        n = m.group('n')
        if n and n.isdigit():
            interval = int(n)
        else:
            # 'every other' means interval 2
            if re.search(r'\bevery\s+other\b', title, re.I):
                interval = 2
            else:
                interval = 1
        freq = freq_map.get(unit.lower(), None)
        if not freq:
            continue
        # get anchor dt using local parser from app.utils
        try:
            from app.utils import parse_date_and_recurrence
            dt_anchor, _rec = parse_date_and_recurrence(title)
            if dt_anchor is None:
                continue
            if dt_anchor.tzinfo is None:
                from datetime import timezone
                dt_anchor = dt_anchor.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        # build rrule
        try:
            rule = _rrule.rrule(freq, dtstart=dt_anchor, interval=interval)
            occs = list(rule.between(win_start, win_end, inc=True))
            for od in occs:
                key = ('todo', occ.get('id'), od.isoformat())
                if key in existing:
                    continue
                existing.add(key)
                additions.append({
                    'occurrence_dt': od.isoformat(),
                    'item_type': occ.get('item_type'),
                    'id': occ.get('id'),
                    'list_id': occ.get('list_id'),
                    'title': occ.get('title'),
                    'dtstart': occ.get('dtstart'),
                    'is_recurring': True,
                    'rrule': f'FREQ={_rrule.__name__}' if False else '',
                    'recurrence_meta': None,
                })
        except Exception:
            pass
    if additions:
        out['occurrences'].extend(additions)
        out['occurrences'].sort(key=lambda x: x.get('occurrence_dt'))
except Exception:
    # best-effort enhancement; ignore failures
    pass

import json
s = json.dumps(out, indent=2, default=str)
if args.output:
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(s)
    print('wrote', args.output, file=sys.stderr)
else:
    print(s)
