#!/usr/bin/env python3
"""Simple e2e check: request the no-js index and assert list id 190 appears."""
import sys
import requests

URL = 'http://127.0.0.1:8000/html_no_js/'
TARGET_ID = 'lists/190'

try:
    r = requests.get(URL, timeout=5)
    r.raise_for_status()
except Exception as e:
    print('REQUEST_FAILED', e)
    sys.exit(2)

if TARGET_ID in r.text:
    print('FOUND')
    sys.exit(0)
else:
    print('NOT_FOUND')
    sys.exit(1)
