#!/usr/bin/env python3
"""Test server end-to-end for creating a recurring todo, verifying occurrence,
ignoring an occurrence, and verifying it's filtered out.

Usage: set BASE_URL (default http://127.0.0.1:8000), USERNAME, PASSWORD env vars or
pass as args. This script uses cookie-based session via /auth/token then sets
cookies for subsequent requests.
"""
import argparse
import requests
import sys
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base-url', default='http://127.0.0.1:8000', help='Base URL of dev server')
    p.add_argument('--username', default='mbaily')
    p.add_argument('--password', default='password')
    p.add_argument('--list-name', default='recurrence-test-auto')
    p.add_argument('--insecure', action='store_true', help='Allow insecure TLS (skip cert verification)')
    args = p.parse_args()

    s = requests.Session()
    verify = not args.insecure
    base = args.base_url.rstrip('/') if args.base_url else ''

    # If the user didn't point to a running server, try common local dev URLs
    tried = []
    if not base:
        candidates = ['http://127.0.0.1:8000', 'http://localhost:8000', 'https://0.0.0.0:10443', 'https://127.0.0.1:10443']
    else:
        candidates = [base]

    auth_ok = False
    token = None
    for c in candidates:
        try:
            print('attempting login at', c)
            r = s.post(f'{c}/auth/token', json={'username': args.username, 'password': args.password}, timeout=5, verify=verify)
        except requests.exceptions.RequestException as e:
            tried.append((c, str(e)))
            continue
        if r.status_code == 200:
            base = c
            token = r.json().get('access_token')
            s.headers.update({'Authorization': f'Bearer {token}'})
            auth_ok = True
            break
        else:
            tried.append((c, f'status={r.status_code}'))

    if not auth_ok:
        print('login failed for all candidates; attempts:')
        for t in tried:
            print(' ', t[0], '->', t[1])
        print('Please ensure the dev server is running and pass --base-url if it listens on a nonstandard port.')
        sys.exit(1)

    # ensure list exists (create via API if missing). Use API calls with the
    # Bearer token so CSRF is not required for automation.
    print('finding or creating list via API...')
    list_id = None
    r = s.get(f'{base}/lists', timeout=5, verify=verify)
    if r.status_code == 200:
        try:
            lists = r.json()
            for L in lists:
                if L.get('name') == args.list_name:
                    list_id = L.get('id')
                    break
        except Exception:
            pass
    if not list_id:
        # create list via API; POST /lists accepts form or query param 'name'.
        # Use query param to avoid form-encoding/CSRF concerns.
        r = s.post(f'{base}/lists', params={'name': args.list_name}, timeout=5, verify=verify)
        if r.status_code not in (200, 201, 303):
            print('list create may have failed, status:', r.status_code, r.text)
        else:
            try:
                j = r.json()
                list_id = j.get('id') or j.get('list_id')
            except Exception:
                # try to re-fetch lists
                rr = s.get(f'{base}/lists', timeout=5, verify=verify)
                if rr.status_code == 200:
                    try:
                        lists = rr.json()
                        for L in lists:
                            if L.get('name') == args.list_name:
                                list_id = L.get('id')
                                break
                    except Exception:
                        pass
    if not list_id:
        print('could not determine list id; will try list_id=1 as fallback')
        list_id = 1

    # create a todo with recurrence text
    todo_text = 'Test recur on 2025-08-25 every 2 weeks'
    payload = {'text': todo_text, 'list_id': list_id}
    print('creating todo via API (form-encoded)...')
    r = s.post(f'{base}/todos', params=payload, verify=verify)
    if r.status_code != 200 and r.status_code != 201:
        print('failed to create todo', r.status_code, r.text)
        sys.exit(1)
    todo = r.json()
    todo_id = todo.get('id') or todo.get('todo_id') or todo.get('id')
    print('created todo id=', todo_id)

    # Allow server to index/parse
    time.sleep(0.5)

    # fetch occurrences window covering the date
    start = '2025-08-01T00:00:00Z'
    end = '2025-09-30T23:59:59Z'
    print('fetching occurrences...')
    r = s.get(f'{base}/calendar/occurrences', params={'start': start, 'end': end}, verify=verify)
    if r.status_code != 200:
        print('occurrences fetch failed', r.status_code, r.text)
        sys.exit(1)
    occs = r.json().get('occurrences', [])
    print('occurrences returned:', len(occs))
    # find occurrence for our todo
    target = None
    for o in occs:
        if int(o.get('id') or 0) == int(todo_id):
            target = o
            break
    if not target:
        print('no occurrence found for created todo; dump sample:')
        print(occs[:10])
        sys.exit(1)
    print('found occurrence:', target['occurrence_dt'], 'hash=', target.get('occ_hash'))

    # call ignore endpoint for this single occurrence
    print('creating ignore scope...')
    ig_resp = s.post(f'{base}/ignore/scope', data={'scope_type': 'occurrence', 'scope_key': target.get('occ_hash')}, verify=verify)
    if ig_resp.status_code != 200:
        print('ignore creation failed', ig_resp.status_code, ig_resp.text)
        sys.exit(1)
    print('ignore created:', ig_resp.json())

    # fetch occurrences again and confirm the target is gone
    r = s.get(f'{base}/calendar/occurrences', params={'start': start, 'end': end}, verify=verify)
    occs2 = r.json().get('occurrences', [])
    found = any((o.get('occ_hash') == target.get('occ_hash')) for o in occs2)
    print('occurrence present after ignore?', found)
    if found:
        sys.exit(1)
    print('test passed: occurrence ignored')

    # --- New: test marking an occurrence as completed ---
    # Create another todo to test completion, or reuse the same todo by
    # creating a fresh occurrence target if necessary. We'll create a new
    # recurrence todo and mark its first occurrence completed.
    todo_text2 = 'Complete-test on 2025-08-20 every week'
    payload2 = {'text': todo_text2, 'list_id': list_id}
    print('creating todo for completion test...')
    r = s.post(f'{base}/todos', params=payload2, verify=verify)
    if r.status_code not in (200, 201):
        print('failed to create todo for completion test', r.status_code, r.text)
        sys.exit(1)
    todo2 = r.json()
    todo2_id = todo2.get('id') or todo2.get('todo_id')
    print('created todo for completion test id=', todo2_id)
    time.sleep(0.5)

    # fetch occurrences and pick first occurrence for this todo
    r = s.get(f'{base}/calendar/occurrences', params={'start': start, 'end': end}, verify=verify)
    occs_all = r.json().get('occurrences', [])
    comp_target = None
    for o in occs_all:
        if int(o.get('id') or 0) == int(todo2_id):
            comp_target = o
            break
    if not comp_target:
        print('no occurrence found for completion test todo; sample:')
        print(occs_all[:10])
        sys.exit(1)
    print('completion target found:', comp_target['occurrence_dt'], 'hash=', comp_target.get('occ_hash'))

    # call occurrence complete endpoint
    print('marking occurrence completed...')
    # The endpoint expects form field 'hash'
    comp_resp = s.post(f'{base}/occurrence/complete', data={'hash': comp_target.get('occ_hash')}, verify=verify)
    if comp_resp.status_code != 200:
        print('occurrence complete failed', comp_resp.status_code, comp_resp.text)
        sys.exit(1)
    print('complete response:', comp_resp.json())

    # re-fetch occurrences and confirm this occ shows completed=True (server
    # marks completed flag for occurrences whose occ_hash is in CompletedOccurrence)
    r = s.get(f'{base}/calendar/occurrences', params={'start': start, 'end': end}, verify=verify)
    occs_after = r.json().get('occurrences', [])
    found_completed = False
    for o in occs_after:
        if o.get('occ_hash') == comp_target.get('occ_hash'):
            if o.get('completed'):
                found_completed = True
            break
    print('occurrence marked completed?', found_completed)
    if not found_completed:
        print('completion flag not observed; server may require include_completed param or different fetch. Dump sample:')
        print([o for o in occs_after if o.get('id') == todo2_id][:5])
        sys.exit(1)
    print('test passed: occurrence completion recorded')


if __name__ == '__main__':
    main()
