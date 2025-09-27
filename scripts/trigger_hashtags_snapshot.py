"""Trigger a server-side snapshot of all hashtags via the debug endpoint.
Usage:
  source .venv/bin/activate
  python scripts/trigger_hashtags_snapshot.py --base https://0.0.0.0:10443 --username mbaily --password mypass \
      [--path debug_logs/hashtags_snapshot.log]

Assumes the dev server is running with login form at /html_no_js/login and session cookie after POST.
"""
import argparse
import re
import sys
import httpx

LOGIN_PATH = '/html_no_js/login'
SNAPSHOT_ENDPOINT = '/debug/write_hashtags_log'

def login_and_get_session(base: str, username: str, password: str) -> httpx.Client:
    client = httpx.Client(base_url=base, verify=False, timeout=30.0, follow_redirects=True)
    # Fetch login page first to set any cookies
    client.get(LOGIN_PATH)
    resp = client.post(LOGIN_PATH, data={'username': username, 'password': password})
    if resp.status_code not in (200, 303, 302):
        raise SystemExit(f'Login failed status={resp.status_code}')
    # Heuristic: ensure some session cookie is present
    if not client.cookies:
        raise SystemExit('No cookies after login; cannot proceed.')
    return client

def trigger_snapshot(client: httpx.Client, path: str):
    # __ALL__ instructs endpoint to dump every hashtag
    r = client.post(SNAPSHOT_ENDPOINT, params={'tags': '__ALL__', 'path': path, 'mode': 'overwrite'})
    if r.status_code != 200:
        raise SystemExit(f'Snapshot failed status={r.status_code} body={r.text[:300]}')
    data = r.json()
    print('Snapshot written:', data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='Base URL e.g. https://0.0.0.0:10443')
    ap.add_argument('--username', required=True)
    ap.add_argument('--password', required=True)
    ap.add_argument('--path', default='debug_logs/hashtags_snapshot.log')
    args = ap.parse_args()
    client = login_and_get_session(args.base, args.username, args.password)
    trigger_snapshot(client, args.path)

if __name__ == '__main__':
    main()
