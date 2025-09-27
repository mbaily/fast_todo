"""Utility script to exercise the /html_no_js/hashtags page (login, list, delete).

Examples:
  List tags:
    python scripts/test_hashtags_page.py --base https://0.0.0.0:10443 --username mbaily --password mypass --list

  Delete specific tags (comma or space separated):
    python scripts/test_hashtags_page.py --base https://0.0.0.0:10443 --username mbaily --password mypass --delete "#alpha,#beta"

  Dry-run delete (no POST):
    python scripts/test_hashtags_page.py --base https://0.0.0.0:10443 --username mbaily --password mypass --delete "#alpha #beta" --dry
"""
import argparse
import sys
import pathlib
import re
import httpx
try:
    from bs4 import BeautifulSoup  # optional
except Exception:
    BeautifulSoup = None  # type: ignore

# NOTE: If bs4 is not installed, run: uv pip install beautifulsoup4

LOGIN_PATH = '/html_no_js/login'
HASHTAGS_PATH = '/html_no_js/hashtags'
DELETE_PATH = '/html_no_js/hashtags/delete'


def login(client: httpx.Client, username: str, password: str):
    # initial GET (set tz cookie maybe)
    r = client.get(LOGIN_PATH)
    if r.status_code not in (200, 303):
        raise SystemExit(f'Login page fetch failed {r.status_code}')
    r = client.post(LOGIN_PATH, data={'username': username, 'password': password})
    # Redirect expected (303) -> index; follow_redirects=True handles it.
    if r.status_code not in (200, 303):
        print('Login HTML response status:', r.status_code)
        print('Login HTML headers:', dict(r.headers))
        print('Login HTML body (first 300):', r.text[:300])
        raise SystemExit(f'Login failed status={r.status_code}')
    # Validate cookies (backend sets session_token + access_token)
    if 'session_token' not in client.cookies:
        # Fallback: try JSON login path to retrieve tokens, then set manually
        r_json = client.post(LOGIN_PATH, data={'username': username, 'password': password}, headers={'Accept':'application/json'})
        print('JSON login attempt status:', r_json.status_code)
        print('JSON login headers:', dict(r_json.headers))
        print('JSON login body (first 300):', r_json.text[:300])
        if r_json.status_code == 200:
            try:
                js = r_json.json()
                if js.get('ok') and js.get('session_token') and js.get('access_token'):
                    client.cookies.set('session_token', js['session_token'])
                    client.cookies.set('access_token', js['access_token'])
                    # csrf token may be needed for delete operations
                    if js.get('csrf_token'):
                        client.cookies.set('csrf_token', js['csrf_token'])
            except Exception:
                pass
        if 'session_token' not in client.cookies:
            raise SystemExit(f'No session_token cookie after login (even after JSON fallback). Cookies present: {list(client.cookies.keys())}')


def fetch_hashtags(client: httpx.Client):
    r = client.get(HASHTAGS_PATH + '?debug=1')
    if r.status_code != 200:
        raise SystemExit(f'Fetch hashtags page failed {r.status_code}')
    text = r.text
    tags: list[str] = []
    if BeautifulSoup:
        soup = BeautifulSoup(text, 'html.parser')
        tags = [a.get('data-tag') for a in soup.select('a.tag-chip[data-tag]') if a.get('data-tag')]
        csrf_input = soup.select_one('input[name="_csrf"]')
        csrf = csrf_input.get('value') if csrf_input else ''
    else:
        # regex fallback
        tags = re.findall(r'data-tag="([^"<>]+)"', text)
        m = re.search(r'name="_csrf"[^>]*value="([^"]+)"', text)
        csrf = m.group(1) if m else ''
    return tags, csrf


def choose_tags(all_tags: list[str], spec: str) -> list[str]:
    if not spec:
        return []
    raw = re.split(r'[\s,]+', spec.strip())
    want = set([t for t in raw if t])
    # keep only those present
    sel = [t for t in all_tags if t in want]
    missing = want - set(sel)
    if missing:
        print(f'Warning: some requested tags not on page: {sorted(missing)}')
    return sel


def delete_tags(client: httpx.Client, csrf: str, tags: list[str], dry: bool):
    if not tags:
        print('No tags to delete.')
        return
    payload = {'_csrf': csrf, 'tags': ','.join(tags)}
    if dry:
        print('[DRY] Would POST delete with', payload)
        return
    r = client.post(DELETE_PATH, data=payload)
    print('Delete POST status:', r.status_code)
    if r.status_code not in (200, 302, 303):
        print('Delete response text (first 300 chars):', r.text[:300])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--username', required=True)
    ap.add_argument('--password', required=True)
    ap.add_argument('--list', action='store_true', help='List hashtags then exit')
    ap.add_argument('--delete', help='Comma or space separated list of tags to delete')
    ap.add_argument('--dry', action='store_true', help='Dry run delete')
    args = ap.parse_args()

    client = httpx.Client(base_url=args.base, timeout=30.0, verify=False, follow_redirects=True)
    login(client, args.username, args.password)
    tags, csrf = fetch_hashtags(client)
    print(f'Total tags on page: {len(tags)}')
    if args.list and not args.delete:
        for t in tags:
            print(' ', t)
        return
    if args.delete:
        sel = choose_tags(tags, args.delete)
        print('Selected for deletion:', sel)
        delete_tags(client, csrf, sel, args.dry)
        # re-fetch to show result
        if not args.dry:
            tags2, _ = fetch_hashtags(client)
            print(f'After deletion, tags count: {len(tags2)}')
            # show diff
            removed = sorted(set(sel) - set(tags2))
            still = sorted(set(sel) & set(tags2))
            if removed:
                print('Removed:', removed)
            if still:
                print('Still present:', still)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Interrupted')
