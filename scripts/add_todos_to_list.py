#!/usr/bin/env python3
"""Utility script to login and add todos to a given list id (supports html_no_js and tailwind login flows).

Quick examples (after venv activated, server running):

    # Preferred (no-js form login converted to JSON by sending Accept header)
    FT_USER=mbaily FT_PASS=mypass \
        python scripts/add_todos_to_list.py \
            --base-url https://0.0.0.0:10443 \
            --login-style nojs \
            --list-id 497 \
            --todos "Alpha" "Beta" "Gamma"

    # Tailwind JSON login variant
    FT_USER=mbaily FT_PASS=mypass \
        python scripts/add_todos_to_list.py \
            --base-url https://0.0.0.0:10443 \
            --login-style tailwind \
            --list-id 497 \
            --todos "One" "Two" --verbose

What it does:
 1. Logs in using the selected style:
        - nojs   -> POST form to /html_no_js/login with Accept: application/json
        - tailwind -> JSON POST to /html_tailwind/login
 2. Captures session/access/csrf cookies (server sets them) in the client cookie jar.
 3. For each provided todo text: POST /todos {text, list_id} using JSON.
 4. Prints per-item success/failure; exits non-zero if any failure.

Flags & behavior:
    --login-style defaults to 'nojs'. Override with environment variable FT_LOGIN_STYLE.
    --note can be supplied multiple times (one total for all, or one per todo).
    --verbose prints cookie summary (values redacted to first 8 chars).

Security guidance:
    - Do NOT hardcode passwords in the script or commit them to version control.
    - Prefer passing credentials via environment variables FT_USER / FT_PASS.
    - Use a throwaway test account for automated local scripts when possible.
    - If using shell history, consider a subshell or env file with restricted permissions.

Exit codes: 0 on full success, 1 if login fails or any todo creation fails.
"""
from __future__ import annotations
import sys, argparse, os, json, time
from typing import List
import httpx

LOGIN_PATH_TAILWIND = "/html_tailwind/login"
LOGIN_PATH_NOJS = "/html_no_js/login"
CREATE_TODO_PATH = "/todos"

def login_tailwind(client: httpx.Client, base_url: str, username: str, password: str) -> dict:
    """JSON login endpoint (tailwind variant)."""
    url = base_url.rstrip('/') + LOGIN_PATH_TAILWIND
    r = client.post(url, json={"username": username, "password": password})
    if r.status_code != 200:
        raise SystemExit(f"Tailwind login failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    if not data.get('ok'):
        raise SystemExit(f"Tailwind login JSON not ok: {data}")
    return data

def login_nojs(client: httpx.Client, base_url: str, username: str, password: str) -> dict:
    """Form login endpoint for html_no_js. We request JSON to get token payload."""
    url = base_url.rstrip('/') + LOGIN_PATH_NOJS
    # Ask for JSON response so we can parse tokens directly
    headers = {"Accept": "application/json"}
    r = client.post(url, data={"username": username, "password": password}, headers=headers)
    if r.status_code != 200:
        raise SystemExit(f"no-js login failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    if not data.get('ok'):
        raise SystemExit(f"no-js login JSON not ok: {data}")
    return data

def create_todo(client: httpx.Client, base_url: str, list_id: int, text: str, note: str | None = None):
    url = base_url.rstrip('/') + CREATE_TODO_PATH
    payload = {"text": text, "list_id": list_id}
    if note:
        payload["note"] = note
    r = client.post(url, json=payload)
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    try:
        data = r.json()
    except Exception as e:
        return False, f"Invalid JSON response: {e}"
    if not data.get('id'):
        return False, f"Unexpected response: {data}"
    return True, data

def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Add todos to a list via API")
    p.add_argument('--base-url', default=os.environ.get('FT_BASE_URL', 'http://127.0.0.1:8000'), help='Base URL, e.g. https://0.0.0.0:10443')
    p.add_argument('--username', default=os.environ.get('FT_USER'), required=True)
    p.add_argument('--password', default=os.environ.get('FT_PASS'), required=True)
    p.add_argument('--list-id', type=int, required=True, help='Target list id')
    p.add_argument('--login-style', choices=['tailwind','nojs'], default=os.environ.get('FT_LOGIN_STYLE','nojs'), help='Which login endpoint style to use (default: nojs)')
    p.add_argument('--verbose', action='store_true', help='Verbose output (show cookies)')
    p.add_argument('--todos', nargs='+', required=True, help='Todo text entries')
    p.add_argument('--note', action='append', help='Optional notes (parallel to todos, repeats)')
    p.add_argument('--timeout', type=float, default=10.0)
    return p.parse_args(argv)

def main(argv: List[str]) -> int:
    args = parse_args(argv)
    # Basic validation
    notes = args.note or []
    if notes and len(notes) not in (1, len(args.todos)):
        print("If providing notes, supply either one note (applied to all) or one per todo", file=sys.stderr)
        return 1

    success = True
    with httpx.Client(timeout=args.timeout, verify=False) as client:  # verify=False since example uses 0.0.0.0:10443 likely self-signed
        if args.login_style == 'tailwind':
            login_data = login_tailwind(client, args.base_url, args.username, args.password)
        else:
            login_data = login_nojs(client, args.base_url, args.username, args.password)
        print(f"Logged in ({args.login_style}) as {args.username}; received keys: {list(login_data.keys())}")
        if args.verbose:
            print("Cookies after login:")
            for c in client.cookies.jar:
                try:
                    print(f"  {c.name}={c.value[:8]}... domain={c.domain} path={c.path}")
                except Exception:
                    pass
        csrf = login_data.get('csrf_token')
        if csrf:
            # Update cookie if not already present (server sets, but be safe)
            client.cookies.set('csrf_token', csrf)
        for idx, text in enumerate(args.todos):
            note = None
            if notes:
                note = notes[0] if len(notes) == 1 else notes[idx]
            ok, data = create_todo(client, args.base_url, args.list_id, text, note)
            if ok:
                print(f"[OK] #{data['id']} '{text}'")
            else:
                print(f"[FAIL] '{text}': {data}")
                success = False
            time.sleep(0.05)  # small delay to vary timestamps
    return 0 if success else 1

if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
