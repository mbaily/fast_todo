#!/usr/bin/env python3
"""Seed a list and 20 todos containing plain date phrases for debugging.

Usage:
  python debuggings/plain_dates_not_showing.py [--db ./fast_todo.db]

This script uses user 'dev_user' with password 'dev'.
It will ensure the DB URL is pointed at the provided sqlite file by setting
DATABASE_URL before importing app modules.
"""
import os
import sys
import argparse
import subprocess
import time
import urllib.request
import urllib.parse
import json
import requests

# make runnable from anywhere
proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if proj_root not in sys.path:
    sys.path.insert(0, proj_root)


# Load canonical phrases and expected datetimes from a JSON file so tests
# and helpers can share the same source of truth. The JSON file contains
# entries [phrase, expected_iso_or_null]. We only need the phrase strings
# here for seeding; the expected datetimes are used by separate checks.
def load_phrases_from_json(path=None):
    if path is None:
        path = os.path.join(os.path.dirname(__file__), 'plain_dates_expected.json')
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            arr = json.load(fh)
            # each item is [phrase, expected_iso_or_null_or_array]
            # return both a list of phrases (for seeding) and a mapping of expectations
            phrases = [item[0] for item in arr]
            expectations = {item[0]: item[1] for item in arr}
            return phrases, expectations
    except Exception:
        return []

PHRASES_AND_EXPECT = load_phrases_from_json(os.path.join(os.path.dirname(__file__), 'plain_dates_expected.json'))
if isinstance(PHRASES_AND_EXPECT, tuple):
    PLAIN_DATE_PHRASES, PLAIN_DATE_EXPECT = PHRASES_AND_EXPECT
else:
    # fallback for older format
    PLAIN_DATE_PHRASES = PHRASES_AND_EXPECT or []
    PLAIN_DATE_EXPECT = {p: None for p in PLAIN_DATE_PHRASES}


# canonical list name used when creating the list for seeded todos
LIST_NAME = 'Debug Plain Dates'


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='Seed test todos with plain dates')
    p.add_argument('--db', default='./fast_todo.db', help='path to sqlite file to use (default ./test.db)')
    p.add_argument('--verify', action='store_true', help='after seeding, verify rows directly in sqlite and print them')
    p.add_argument('--wipe-db', action='store_true', help='delete the target sqlite file before seeding (USE WITH CAUTION)')
    p.add_argument('--extract', action='store_true', help='run app.utils.extract_dates on each seeded phrase and print results')
    p.add_argument('--verbose', action='store_true', help='print a sorted table of phrase, extract_dates result, and matched seeded todo text')
    p.add_argument('--server', action='store_true', help='start a dev uvicorn server and query calendar endpoints (will be killed when done)')
    return p.parse_args(argv)


async def ensure_dev_user(username='dev_user', password='dev'):
    # import lazily
    from app.db import init_db, async_session
    from app.models import User
    from app.auth import pwd_context
    from sqlmodel import select

    await init_db()
    ph = pwd_context.hash(password)
    async with async_session() as sess:
        q = await sess.exec(select(User).where(User.username == username))
        u = q.first()
        if u:
            # update password to known value so script can run repeatedly
            u.password_hash = ph
            sess.add(u)
            await sess.commit()
            await sess.refresh(u)
            return u
        u = User(username=username, password_hash=ph, is_admin=False)
        sess.add(u)
        await sess.commit()
        await sess.refresh(u)
        return u


async def seed_todos(username='dev_user'):
    from app.db import init_db, async_session
    from app.models import ListState, Todo, User
    from sqlmodel import select
    await init_db()
    async with async_session() as sess:
        q = await sess.exec(select(User).where(User.username == username))
        u = q.first()
        if not u:
            raise RuntimeError('dev_user not found; run ensure_dev_user first')
        # create a list
        l = ListState(name='Debug Plain Dates', owner_id=u.id)
        sess.add(l)
        await sess.commit()
        await sess.refresh(l)

        # use module-level phrases so other test helpers can reference them
        phrases = PLAIN_DATE_PHRASES

        for i, text in enumerate(phrases, start=1):
            # Todo model expects 'text' and 'list_id' (no title or owner_id fields)
            t = Todo(text=f'PD-{i}: {text}', list_id=l.id)
            sess.add(t)
        await sess.commit()
        print(f'Created list id={l.id} and {len(phrases)} todos')


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    db_path = args.db
    # normalize db_file path for local sqlite operations
    db_file = db_path.replace('sqlite+aiosqlite:///', '') if db_path.startswith('sqlite+aiosqlite:///') else db_path
    # optional wipe of DB file for clean seed
    if getattr(args, 'wipe_db', False) or getattr(args, 'wipe-db', False):
        db_file = db_path.replace('sqlite+aiosqlite:///', '') if db_path.startswith('sqlite+aiosqlite:///') else db_path
        if os.path.exists(db_file):
            print(f"Removing DB file {db_file} as requested (--wipe-db)")
            os.remove(db_file)
    if db_path:
        os.environ['DATABASE_URL'] = f"sqlite+aiosqlite:///{db_path}"
    import asyncio
    # ensure dev user and seed
    asyncio.run(ensure_dev_user())
    asyncio.run(seed_todos())
    # defaults for optional reporting blocks
    rows = []
    extraction_nonmatch_count = 0
    if getattr(args, 'verify', False):
        # Query sqlite directly to verify the seeded rows; print a concise summary
        rows = get_seeded_todos(db_path, LIST_NAME)
    confirmed = count_confirmed_phrases(db_path, PLAIN_DATE_PHRASES, LIST_NAME)
    if getattr(args, 'verify', False):
        print(f"DB OK: {db_file} — {len(rows)} todos in list '{LIST_NAME}'")
    print(f"Confirmed phrases present: {confirmed}/{len(PLAIN_DATE_PHRASES)}")
    if getattr(args, 'extract', False):
        # Run the app's extract_dates helper on each phrase and print results
        from app.utils import extract_dates
        # Run extractor but only print non-matching seeded phrases per requirement.
        matched_count = 0
        non_matching_phrases: list[str] = []
        # load seeded todo texts for mapping
        seeded_rows = get_seeded_todos(db_path, LIST_NAME)
        seeded_texts = [r[1] for r in seeded_rows]
        verbose_rows: list[tuple[str, str, str]] = []
        for phrase in PLAIN_DATE_PHRASES:
            try:
                results = extract_dates(phrase)
            except Exception:
                results = []
            # Normalize results to a consistent ISO-Z format (YYYY-MM-DDTHH:MM:SSZ)
            from datetime import datetime, timezone

            def _normalize_dt_obj(x):
                # Accept datetime-like objects and return YYYY-MM-DDTHH:MM:SSZ
                try:
                    if hasattr(x, 'astimezone'):
                        dt = x.astimezone(timezone.utc)
                        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
                except Exception:
                    pass
                return None

            def _normalize_iso_str(s):
                # Accept an ISO-ish string and normalize to YYYY-MM-DDTHH:MM:SSZ
                if s is None:
                    return None
                if isinstance(s, str):
                    # allow trailing Z or +00:00
                    try:
                        st = s
                        if st.endswith('Z'):
                            st = st[:-1] + '+00:00'
                        # fromisoformat supports +00:00 but not lone Z
                        dt = datetime.fromisoformat(st)
                        dt = dt.astimezone(timezone.utc)
                        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
                    except Exception:
                        return s
                return str(s)

            iso_results: list[str] = []
            for d in (results or []):
                n = _normalize_dt_obj(d)
                if n:
                    iso_results.append(n)
                else:
                    # fallback to string form
                    try:
                        iso_results.append(str(d))
                    except Exception:
                        pass

            expected = PLAIN_DATE_EXPECT.get(phrase)
            # expected may be None, a string, or a list; normalize to list of normalized strings
            expected_list: list[str] = []
            if expected is None:
                expected_list = []
            elif isinstance(expected, list):
                expected_list = [e for e in (_normalize_iso_str(x) for x in expected) if e]
            else:
                ne = _normalize_iso_str(expected)
                expected_list = [ne] if ne else []

            # consider it matched if extractor returned at least one result
            # and any expected ISO is present in iso_results (if expectations provided)
            is_matched = False
            if iso_results:
                if expected_list:
                    # check for intersection (normalized)
                    if any(e in iso_results for e in expected_list):
                        is_matched = True
                else:
                    # no explicit expectation provided: treat any extraction as match
                    is_matched = True

            if is_matched:
                matched_count += 1
            else:
                non_matching_phrases.append(phrase)
            # capture verbose row: phrase, str(results), matched todo text (or '')
            matched_text = ''
            for t in seeded_texts:
                if phrase in t:
                    matched_text = t
                    break
            verbose_rows.append((phrase, iso_results, expected, matched_text))
        # print non-matching seeded phrases and a count
        print('\nNon-matching seeded phrases (no extracted dates):')
        for p in non_matching_phrases:
            print(f"- {p}")
        extraction_nonmatch_count = len(non_matching_phrases)
        print(f"\nExtraction non-match count: {extraction_nonmatch_count}/{len(PLAIN_DATE_PHRASES)}")
        if getattr(args, 'verbose', False):
            # print a simple table sorted by phrase
            print('\nVerbose extraction table (phrase | extract_dates -> | matched todo text)')
            for row in sorted(verbose_rows, key=lambda r: r[0].lower()):
                ph, res_iso, expected_val, mt = row
                print(f"{ph} | extracted={res_iso} | expected={expected_val} | todo={mt}")
    # Always print a concise final summary at the end so the non-match count
    # is visible after the verbose table (or when verbose is off).
    print('\nFinal summary:')
    print(f"Extraction non-match count: {extraction_nonmatch_count}/{len(PLAIN_DATE_PHRASES)}")
    if getattr(args, 'server', False):
            # start uvicorn dev server as subprocess on an ephemeral port
            host = '127.0.0.1'
            port = 8001
            cmd = [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', host, '--port', str(port), '--log-level', 'warning']
            print(f"Starting dev server: {' '.join(cmd)}")
            # start subprocess inheriting current environment (do not override ENABLE_RECURRING_DETECTION)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy())
            try:
                # wait for server up (poll /manifest.json)
                base = f'http://{host}:{port}'
                timeout = 10.0
                start = time.time()

                # wait for server up (poll /manifest.json)
                start = time.time()
                while True:
                    try:
                        r = requests.get(base + '/manifest.json', timeout=1.0)
                        if r.status_code == 200:
                            break
                    except Exception:
                        pass
                    if time.time() - start > timeout:
                        raise RuntimeError('server did not start in time')
                    time.sleep(0.2)

                # Query calendar/occurrences for September 2025
                start_iso = '2025-09-01T00:00:00Z'
                end_iso = '2025-09-30T23:59:59Z'
                url = f'{base}/calendar/occurrences'
                params = {'start': start_iso, 'end': end_iso}
                print(f'Querying {url} for {start_iso}..{end_iso}')
                # authenticate as dev_user to get access token
                token = None
                try:
                    auth_url = base + '/auth/token'
                    ar = requests.post(auth_url, json={'username': 'dev_user', 'password': 'dev'}, timeout=5.0)
                    if ar.status_code == 200:
                        token = ar.json().get('access_token')
                    else:
                        print('Auth failed status:', ar.status_code, 'body:', ar.text)
                except Exception as e:
                    print('Auth request failed:', e)

                headers = {'Authorization': f'Bearer {token}'} if token else {}
                try:
                    r = requests.get(url, params=params, headers=headers, timeout=10.0)
                except Exception as e:
                    print('Request failed:', e)
                    r = None
                server_occurrences_count = None
                server_response_summary = None
                if r is None:
                    print('Server /calendar/occurrences response status: None')
                    print('Response body: None')
                    server_response_summary = None
                else:
                    try:
                        j = r.json()
                    except Exception:
                        j = r.text
                    print('Server /calendar/occurrences response status:', r.status_code)
                    if isinstance(j, dict) and 'occurrences' in j:
                        server_occurrences_count = len(j.get('occurrences') or [])
                        server_response_summary = {'occurrences': server_occurrences_count, 'truncated': j.get('truncated', False)}
                        print(f"Occurrences returned: {server_occurrences_count}")
                        print('Sample:', (j.get('occurrences') or [])[:5])
                    elif isinstance(j, list):
                        server_occurrences_count = len(j)
                        server_response_summary = {'occurrences': server_occurrences_count}
                        print(f'Occurrences returned: {server_occurrences_count}')
                        print('Sample:', j[:5])
                    else:
                        server_response_summary = j
                        print('Response body:', j)
            finally:
                print('Stopping dev server')
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
            # final summary after server run so it's visible after the verbose table
            print('\nFinal summary:')
            print(f"Extraction non-match count: {extraction_nonmatch_count}/{len(PLAIN_DATE_PHRASES)}")
            if 'server_occurrences_count' in locals() and server_occurrences_count is not None:
                print(f"Server /calendar/occurrences returned: {server_occurrences_count} occurrences")
            else:
                print('Server /calendar/occurrences returned: (no data)')


def get_seeded_todos(db_path='./fast_todo.db', list_name=LIST_NAME):
    """Query the sqlite DB directly for todos in the seeded list and return rows.

    Returns list of tuples: (todo.id, todo.text, liststate.id, liststate.name)
    """
    import sqlite3
    db_file = db_path.replace('sqlite+aiosqlite:///', '') if db_path.startswith('sqlite+aiosqlite:///') else db_path
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    # find the list id
    cur.execute("SELECT id FROM liststate WHERE name = ?", (list_name,))
    row = cur.fetchone()
    if not row:
        return []
    list_id = row[0]
    cur.execute("SELECT id, text, list_id FROM todo WHERE list_id = ? ORDER BY id", (list_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def count_confirmed_phrases(db_path, phrases, list_name=LIST_NAME):
    """Return number of phrases that are found as substrings in todos for list_name."""
    rows = get_seeded_todos(db_path, list_name)
    texts = [r[1] for r in rows]
    confirmed = 0
    for phrase in phrases:
        # match phrase as substring in any todo text
        if any(phrase in t for t in texts):
            confirmed += 1
    return confirmed


if __name__ == '__main__':
    main()

