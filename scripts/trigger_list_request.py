#!/usr/bin/env python3
"""Trigger a GET to the dedicated list page to cause server-side rendering and SUBLETS_DUMP.

Usage: set TARGET_URL env var to override default. Writes output to /tmp/list_190.html
"""
import os
import sys
import urllib.request

url = os.getenv('TARGET_URL') or 'http://127.0.0.1:8000/html_no_js/lists/190'
out = '/tmp/list_190.html'

try:
    with urllib.request.urlopen(url, timeout=15) as r:
        data = r.read()
    with open(out, 'wb') as f:
        f.write(data)
    print('fetched', url, '->', out)
    sys.exit(0)
except Exception as e:
    print('error fetching', url, e, file=sys.stderr)
    sys.exit(2)
