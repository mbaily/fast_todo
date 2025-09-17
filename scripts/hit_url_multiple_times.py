#!/usr/bin/env python3
"""
Small helper to hit a URL N times and summarize Jinja cache stats headers.

Requires the server to be running and started with env:
  JINJA_CACHE_STATS=1 PROFILE_REQUESTS=1 (profiling optional)

Example:
  source .venv/bin/activate
  python scripts/hit_url_multiple_times.py --url https://localhost:8443/html_no_js/ --times 30 --insecure
"""
import argparse
import sys
from urllib.parse import urlparse

try:
    import requests
except Exception:
    print("This script requires the 'requests' package. Install it in your venv.")
    sys.exit(2)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', required=True)
    p.add_argument('--times', type=int, default=30)
    p.add_argument('--insecure', action='store_true', help='Skip TLS verification')
    args = p.parse_args()

    sess = requests.Session()
    verify = not args.insecure

    totals = {
        'Get-Calls': 0,
        'Load-Calls': 0,
        'Unique-Templates': 0,
    }
    samples = []

    for i in range(args.times):
        try:
            r = sess.get(args.url, verify=verify, timeout=30)
        except Exception as e:
            print(f"request {i+1} failed: {e}")
            break

        # Extract Jinja cache headers (if enabled on server)
        h = r.headers
        prefix = 'X-Jinja-'
        def _get_int(key):
            try:
                return int(h.get(prefix + key, '0'))
            except Exception:
                return 0

        get_calls = _get_int('Get-Calls')
        load_calls = _get_int('Load-Calls')
        uniq = _get_int('Unique-Templates')
        pct_unique = h.get(prefix + 'Compile-Percent-Unique')
        pct_calls = h.get(prefix + 'Compile-Percent-Calls')

        samples.append((get_calls, load_calls, uniq, pct_unique, pct_calls))
        totals['Get-Calls'] += get_calls
        totals['Load-Calls'] += load_calls
        totals['Unique-Templates'] += uniq

        print(f"{i+1:02d}: status={r.status_code} get={get_calls} load={load_calls} uniq={uniq} pctU={pct_unique} pctC={pct_calls}")

    n = len(samples)
    if n:
        avg_get = totals['Get-Calls'] / n
        avg_load = totals['Load-Calls'] / n
        avg_uniq = totals['Unique-Templates'] / n
        pct_load_per_get = (100.0 * avg_load / avg_get) if avg_get else 0.0
        print("\nSummary:")
        print(f"  samples={n}")
        print(f"  avg get_calls={avg_get:.2f}")
        print(f"  avg load_calls={avg_load:.2f}")
        print(f"  avg unique_templates={avg_uniq:.2f}")
        print(f"  avg compile% per-call={pct_load_per_get:.2f}%  (lower means more cache hits)")
        # Show last sample detail (likely steady-state)
        g,l,u,pu,pc = samples[-1]
        print(f"  last: get={g} load={l} uniq={u} pctU={pu} pctC={pc}")
    else:
        print("No successful samples with Jinja headers were collected.")


if __name__ == '__main__':
    main()
