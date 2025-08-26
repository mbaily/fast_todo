#!/usr/bin/env python3
"""
Fetch the HTML calendar page for a given year and month from the dev server.

Usage:
  ./scripts/fetch_calendar_page.py 2025 9
  ./scripts/fetch_calendar_page.py 2025 9 --host 0.0.0.0 --port 10443 --insecure

Defaults: host=0.0.0.0, port=10443, path=/html_no_js/calendar

If the server uses a self-signed cert, pass --insecure to skip verification.
"""
import argparse
import sys
import urllib.parse
import ssl

parser = argparse.ArgumentParser(description='Fetch calendar HTML page for a month')
parser.add_argument('year', type=int, help='year (numeric)')
parser.add_argument('month', type=int, help='month (numeric 1-12)')
parser.add_argument('--host', default='0.0.0.0', help='server host (default 0.0.0.0)')
parser.add_argument('--port', default=10443, type=int, help='server port (default 10443)')
parser.add_argument('--path', default='/html_no_js/calendar', help='URL path (default /html_no_js/calendar)')
parser.add_argument('--insecure', action='store_true', help='ignore TLS certificate verification')
parser.add_argument('--output', help='write output HTML to file instead of stdout')
parser.add_argument('--session-token', help='session_token cookie value for authenticated requests')
parser.add_argument('--auth-bearer', help='Authorization: Bearer <token> header value')
parser.add_argument('--username', help='username to login with (will POST to /auth/token)')
parser.add_argument('--password', help='password for login')
args = parser.parse_args()

year = args.year
month = args.month
if not (1 <= month <= 12):
    print('invalid month, must be 1-12', file=sys.stderr)
    sys.exit(2)

query = {'year': str(year), 'month': str(month)}
qs = urllib.parse.urlencode(query)
url = f"https://{args.host}:{args.port}{args.path}?{qs}"

# Try to use requests if available (more convenient); otherwise fallback to urllib
try:
    import requests
    import warnings
    from urllib3.exceptions import InsecureRequestWarning
    if args.insecure:
        warnings.filterwarnings('ignore', InsecureRequestWarning)
    headers = {}
    cookies = None
    # If username/password provided, perform login to obtain bearer token
    if args.username and args.password:
        login_url = f"https://{args.host}:{args.port}/auth/token"
        try:
            r = requests.post(login_url, json={'username': args.username, 'password': args.password}, verify=not args.insecure, timeout=10)
            r.raise_for_status()
            token = r.json().get('access_token')
            headers['Authorization'] = f'Bearer {token}'
        except Exception as e:
            print('login failed:', e, file=sys.stderr)
            sys.exit(3)
    elif args.auth_bearer:
        headers['Authorization'] = f'Bearer {args.auth_bearer}'
    if args.session_token:
        cookies = {'session_token': args.session_token}
    resp = requests.get(url, verify=not args.insecure, timeout=10, headers=headers, cookies=cookies)
    print(f'HTTP {resp.status_code} {resp.reason}', file=sys.stderr)
    content = resp.text
except Exception:
    # fallback to urllib
    import urllib.request
    try:
        if args.insecure:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        else:
            ctx = None
        headers = {}
        # If username/password provided, perform login via urllib
        if args.username and args.password:
            import json
            login_url = f"https://{args.host}:{args.port}/auth/token"
            login_req = urllib.request.Request(login_url, data=bytes(json.dumps({'username': args.username, 'password': args.password}), 'utf-8'), headers={'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(login_req, context=ctx, timeout=10) as lr:
                    j = json.load(lr)
                    token = j.get('access_token')
                    if token:
                        headers['Authorization'] = f'Bearer {token}'
            except Exception as e:
                print('login failed:', e, file=sys.stderr)
                sys.exit(3)
        if args.auth_bearer:
            headers['Authorization'] = f'Bearer {args.auth_bearer}'
        if args.session_token:
            headers['Cookie'] = f'session_token={args.session_token}'
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            content_bytes = r.read()
            content = content_bytes.decode('utf-8', errors='replace')
            print(f'HTTP {r.status} {r.reason}', file=sys.stderr)
    except Exception as e:
        print('failed to fetch URL:', e, file=sys.stderr)
        sys.exit(3)

if args.output:
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(content)
    print('wrote', args.output, file=sys.stderr)
else:
    sys.stdout.write(content)
