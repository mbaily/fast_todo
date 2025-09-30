#!/usr/bin/env python3
"""Fetch recent debug FIFO entries from the running server and save to a file.

Usage: python scripts/fetch_debug_logs.py --base https://localhost:10443 --out debug_logs/fifo_dump.json
"""
import argparse
import json
import sys
from urllib.parse import urljoin

try:
    import requests
except Exception:
    print('requests required. Install with: pip install requests', file=sys.stderr)
    raise

def fetch(base, limit=200, clear=0, out=None, auth=None, verify=True):
    url = urljoin(base, '/debug/log_fifo')
    params = {'limit': limit, 'clear': int(clear)}
    r = requests.get(url, params=params, auth=auth, verify=verify, timeout=10)
    r.raise_for_status()
    j = r.json()
    if out:
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(j, f, indent=2, ensure_ascii=False)
        print('Wrote', out)
    else:
        print(json.dumps(j, indent=2, ensure_ascii=False))

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base', required=True)
    p.add_argument('--out', help='Output file (JSON)')
    p.add_argument('--limit', type=int, default=200)
    p.add_argument('--clear', action='store_true')
    p.add_argument('--no-verify', action='store_true', help='Disable TLS verification')
    args = p.parse_args()
    fetch(args.base, limit=args.limit, clear=args.clear, out=args.out, verify=not args.no_verify)

if __name__ == '__main__':
    main()
