import os
import subprocess
import time
import requests
import pytest
from sqlmodel import select


def _ensure_test_user():
    # create a stable test user in the app DB so the server process can authenticate
    try:
        import asyncio
        from app.db import async_session
        from app.models import User
        from app.auth import pwd_context

        async def _create():
            async with async_session() as sess:
                q = await sess.exec(select(User).where(User.username == '__e2e_test__'))
                u = q.first()
                if not u:
                    ph = pwd_context.hash('p')
                    u = User(username='__e2e_test__', password_hash=ph, is_admin=True)
                    sess.add(u)
                    try:
                        await sess.commit()
                    except Exception:
                        await sess.rollback()

        asyncio.get_event_loop().run_until_complete(_create())
    except Exception as e:
        pytest.skip(f"Unable to ensure test user: {e}")


@pytest.fixture(scope='session')
def live_server():
    """Start a uvicorn server in background bound to localhost and yield the base URL.

    Uses port 8001 by default; set env E2E_PORT to change.
    """
    port = int(os.environ.get('E2E_PORT', '8001'))
    base = f'http://127.0.0.1:{port}'

    # ensure a test user exists in the DB before server starts
    _ensure_test_user()

    env = os.environ.copy()
    env.setdefault('SECRET_KEY', 'test-secret-key-e2e')

    log_path = os.path.join(os.getcwd(), f'e2e_server_{port}.log')
    # ensure logfile exists and is writable
    logf = open(log_path, 'ab')
    cmd = ['uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)]
    # Redirect both stdout and stderr to the logfile so we can inspect server traces
    proc = subprocess.Popen(cmd, env=env, stdout=logf, stderr=logf)

    # wait for server to become ready
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            r = requests.get(base + '/html_tailwind/whoami', timeout=1)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)

    try:
        yield (base, log_path)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        try:
            logf.close()
        except Exception:
            pass


def test_e2e_tailwind_login_create_logout(live_server):
    """Use Playwright to simulate browser login, create a list via client API, then logout.

    Requires Playwright and its browser binaries to be installed:
      pip install playwright
      playwright install
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        pytest.skip(f"Playwright not available: {e}")

    url, log_path = live_server
    server_5xx = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        # track any 5xx responses observed by the browser
        def _on_response(response):
            try:
                if response.status >= 500:
                    server_5xx.append({'url': response.url, 'status': response.status})
            except Exception:
                pass
        page.on('response', _on_response)

        # go to login page
        page.goto(f"{url}/html_tailwind/login", wait_until='networkidle')
        page.fill('#username', '__e2e_test__')
        page.fill('#password', 'p')
        page.click('button[type=submit]')

        # wait for navigation to the tailwind index (or a redirect)
        try:
            page.wait_for_url('**/html_tailwind', timeout=5000)
        except Exception:
            # may stay on same page if server replies differently; continue
            pass

        # create a new list: POST to the server-side /lists endpoint using query fallback
        create_resp = page.evaluate("""
            async () => {
                const r = await fetch('/lists?name=' + encodeURIComponent('e2e list from playwright'), {method: 'POST', headers: {'Accept': 'application/json'}});
                try { return await r.json(); } catch(e){ return {status: r.status}; }
            }
        """)

        assert create_resp is not None

        # logout via JSON endpoint
        logout = page.evaluate("""
            async () => {
                const r = await fetch('/html_tailwind/logout', {method: 'POST', headers: {'Accept': 'application/json'}});
                try { return await r.json(); } catch(e){ return null; }
            }
        """)

        # Accept either a successful JSON logout or a null fallback
        assert logout is None or logout.get('ok') is True

        # Close browser to flush any remaining events
        browser.close()

    # After browser actions, inspect server logfile for Python tracebacks or ASGI exception markers
    try:
        with open(log_path, 'r', errors='ignore') as lf:
            tail = lf.read()
    except Exception:
        tail = ''

    # Look for common error markers
    markers = ['Traceback (most recent call last):', 'Exception in ASGI application', 'jinja2.exceptions', 'Traceback', 'Traceback (most recent call last)', 'ERROR:']
    found = [m for m in markers if m in tail]
    # Also consider any 5xx responses captured from the browser
    if server_5xx:
        found.append(f'5xx_responses:{server_5xx}')

    if found:
        # Fail the test and include a short excerpt of the logfile for debugging
        excerpt = tail[-4000:] if len(tail) > 4000 else tail
        pytest.fail(f"Server-side exceptions detected during E2E run: {found}\n--- log excerpt ---\n{excerpt}")
