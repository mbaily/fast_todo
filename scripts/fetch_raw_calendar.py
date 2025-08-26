#!/usr/bin/env python3
"""Fetch raw HTML for the calendar page using saved real-browser diagnostics and report whether a specific todo anchor is present."""
import json
import os
from pathlib import Path
import requests

DIAG = Path('screenshots/real_browser_diag.json')
URL = os.environ.get('BASE_URL', 'https://0.0.0.0:10443')
TARGET_PATH = '/html_no_js/calendar?year=2030&month=11'
TARGET_TODO_ID = 55


def load_diag():
    if not DIAG.exists():
        return None
    with DIAG.open('r') as f:
        return json.load(f)


def build_session(diag):
    s = requests.Session()
    # set UA if available
    ua = None
    if diag:
        ua = diag.get('userAgent') or diag.get('ua')
    if ua:
        s.headers.update({'User-Agent': ua})
    # load cookies if present
    if diag and 'cookies' in diag:
        for c in diag['cookies']:
            # cookies captured by puppeteer include name, value, domain, path
            try:
                s.cookies.set(c.get('name'), c.get('value'), domain=c.get('domain'), path=c.get('path'))
            except Exception:
                try:
                    s.cookies.set(c.get('name'), c.get('value'))
                except Exception:
                    pass
    return s


def main():
    diag = load_diag()
    s = build_session(diag)
    url = f"{URL}{TARGET_PATH}"
    print('Fetching raw HTML from', url)
    try:
        # Allow insecure for local dev TLS if needed
        r = s.get(url, verify=False, timeout=15)
    except Exception as e:
        print('Fetch failed:', e)
        return 2
    print('HTTP', r.status_code)
    text = r.text or ''
    anchor = f'/html_no_js/todos/{TARGET_TODO_ID}'
    if anchor in text:
        # show surrounding snippet
        idx = text.find(anchor)
        start = max(0, text.rfind('<', 0, idx-100))
        end = text.find('>', idx+100)
        snippet = text[start:end+1] if start >=0 and end >=0 else text[max(0, idx-80):idx+80]
        print('Anchor found in raw HTML. Snippet:')
        print(snippet)
        return 0
    else:
        print('Anchor NOT found in raw HTML.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
