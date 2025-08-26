#!/usr/bin/env python3
"""Simple script to authenticate, fetch occurrences, POST to /occurrence/complete using Bearer token,
and verify the completed flag is set for that occ_hash.

Usage:
  PYTHONPATH=$(pwd) .venv/bin/python3 scripts/call_complete_direct.py --base https://127.0.0.1:10443 --username mbaily --password my-secret-pass --insecure
"""
import argparse
import requests
import sys
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base', default='https://127.0.0.1:10443')
    p.add_argument('--username', default='mbaily')
    p.add_argument('--password', default='password')
    p.add_argument('--insecure', action='store_true')
    args = p.parse_args()

    base = args.base.rstrip('/')
    verify = not args.insecure
    s = requests.Session()

    print('authenticating')
    r = s.post(f'{base}/auth/token', json={'username': args.username, 'password': args.password}, verify=verify)
    if r.status_code != 200:
        print('auth failed', r.status_code, r.text)
        sys.exit(1)
    token = r.json().get('access_token')
    s.headers.update({'Authorization': f'Bearer {token}'})
    print('got token')

    # create a todo to test completion
    print('creating test todo')
    r = s.post(f'{base}/todos', params={'text': 'direct-complete-test on 2025-08-20 weekly', 'list_id': 1}, verify=verify)
    if r.status_code not in (200,201):
        print('failed to create todo', r.status_code, r.text)
        sys.exit(1)
    todo = r.json()
    todo_id = todo.get('id') or todo.get('todo_id') or todo.get('id')
    print('created todo id', todo_id)
    time.sleep(0.3)

    # fetch occurrences
    start = '2025-08-01T00:00:00Z'
    end = '2025-09-30T23:59:59Z'
    r = s.get(f'{base}/calendar/occurrences', params={'start': start, 'end': end}, verify=verify)
    if r.status_code != 200:
        print('occ fetch failed', r.status_code, r.text)
        sys.exit(1)
    occs = r.json().get('occurrences', [])
    target = None
    for o in occs:
        if int(o.get('id') or 0) == int(todo_id):
            target = o
            break
    if not target:
        print('no occurrence found for todo', todo_id)
        sys.exit(1)
    print('target occ:', target['occurrence_dt'], 'hash=', target.get('occ_hash'))

    # POST /occurrence/complete with Bearer token
    print('posting completion')
    cr = s.post(f'{base}/occurrence/complete', data={'hash': target.get('occ_hash')}, verify=verify)
    print('completion response', cr.status_code, cr.text)
    if cr.status_code != 200:
        sys.exit(1)

    # refetch occurrences and verify completed flag
    r = s.get(f'{base}/calendar/occurrences', params={'start': start, 'end': end}, verify=verify)
    occs2 = r.json().get('occurrences', [])
    found_completed = False
    for o in occs2:
        if o.get('occ_hash') == target.get('occ_hash'):
            print('found after complete, completed=', o.get('completed'))
            if o.get('completed'):
                found_completed = True
            break
    print('verified completed?', found_completed)
    if not found_completed:
        sys.exit(2)

    print('done')


if __name__ == '__main__':
    main()
