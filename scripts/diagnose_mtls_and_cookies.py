#!/usr/bin/env python3
"""
Diagnostic script to compare TLS / cookie behavior against two targets.

Usage (edit the variables below inside the script):
- Set USERNAME and PASSWORD strings inside this file (the user asked for credentials
  to be embedded in the script, not passed on the command line).
- Edit the TARGETS list to contain two entries: one for production and one for dev.

The script will:
- Run an openssl s_client probe to see whether the server requests a client certificate.
- Run several curl probes (direct IP, with SNI using --resolve) and save verbose output.
- Use the Python requests.Session() to POST the login form (verify=False to accept self-signed)
  and report response headers and cookies saved by the session.
- Write outputs into `scripts/diagnose_outputs/` and print a summary to stdout.

Note: this script runs local network commands and external programs (openssl, curl).
Make sure those are available on the machine where you run it.

"""

from __future__ import annotations
import subprocess
import sys
import os
from pathlib import Path
import shutil
import json
import socket
import ssl
import time
from typing import Optional, Dict, List

# ======= CONFIGURE ========
# Username/password are loaded from a JSON config file placed next to this script:
# scripts/diagnose_mtls_and_cookies.config
# The file should contain: {"username": "youruser", "password": "yourpass"}
USERNAME = ""
PASSWORD = ""
CONFIG_FILE = Path(__file__).parent / "diagnose_mtls_and_cookies.config"
CLIENT_CERT: Optional[str] = None
CLIENT_KEY: Optional[str] = None

# Targets to test. Fill these with the exact hostnames/ips you use in the browser.
# Each entry is a dict with keys: name, host (the hostname shown in the browser address bar), ip, port
# Example:
# {"name":"prod","host":"git2","ip":"203.222.147.179","port":443}
# {"name":"dev","host":"127.0.0.1","ip":"127.0.0.1","port":8000}
TARGETS = [
    {"name": "production", "host": "203.222.147.179", "ip": "203.222.147.179", "port": 10443},
    {"name": "dev", "host": "127.0.0.1", "ip": "127.0.0.1", "port": 10443},
]

LOGIN_PATH = "/html_no_js/login"  # path that accepts POST username/password form
OUTPUT_DIR = Path(__file__).parent / "diagnose_outputs"
OPENSSL_BIN = shutil.which("openssl") or "openssl"

# ===========================

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def save(fname: str, data: str, mode: str = "w") -> None:
    path = OUTPUT_DIR / fname
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def run_subprocess(cmd: List[str], timeout: int = 2) -> Dict:
    """Run subprocess and capture stdout/stderr and return dict."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except subprocess.TimeoutExpired as e:
        return {"returncode": -1, "stdout": e.stdout or "", "stderr": f"TIMEOUT after {timeout}s"}


def raw_http_request_via_socket(host_for_sni: str, connect_ip: str, port: int, path: str, method: str = "GET", data: str = "", headers: Optional[Dict[str, str]] = None, use_tls: bool = True, timeout: int = 2) -> Dict:
    """Perform an HTTP request (GET/POST) by opening a TCP socket to connect_ip:port and (optionally) wrapping with TLS
    while setting SNI to host_for_sni. Returns a dict with returncode, status_code, response_headers, body and raw_text.
    """
    try:
        addr = (connect_ip, port)
        sock = socket.create_connection(addr, timeout=timeout)
    except Exception as e:
        return {"returncode": 7, "error": "connect_failed", "exception": str(e)}

    conn = None
    tls_info: Dict = {}
    try:
        if use_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            try:
                # Offer both http/2 and http/1.1 like modern clients do; server may pick one or none.
                ctx.set_alpn_protocols(["h2", "http/1.1"])
            except Exception:
                pass
            # Optionally load client cert from config
            if CLIENT_CERT:
                try:
                    ctx.load_cert_chain(certfile=CLIENT_CERT, keyfile=CLIENT_KEY)
                except Exception:
                    pass
            ss = ctx.wrap_socket(sock, server_hostname=host_for_sni)
            # collect TLS handshake info
            try:
                tls_info["cipher"] = ss.cipher()
            except Exception:
                tls_info["cipher"] = None
            try:
                tls_info["version"] = ss.version()
            except Exception:
                tls_info["version"] = None
            try:
                tls_info["alpn"] = ss.selected_alpn_protocol()
            except Exception:
                tls_info["alpn"] = None
            try:
                tls_info["peer_cert"] = ss.getpeercert()
            except Exception:
                tls_info["peer_cert"] = None
            conn = ss
        else:
            conn = sock

        # Build request
        h = {"Host": host_for_sni, "User-Agent": "diagnose-script/1.0", "Accept": "*/*", "Connection": "close"}
        if headers:
            h.update(headers)
        body = data or ""
        if method.upper() == "POST" and "Content-Type" not in h:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        if method.upper() == "POST":
            h["Content-Length"] = str(len(body))

        req_lines = [f"{method} {path} HTTP/1.1"]
        for k, v in h.items():
            req_lines.append(f"{k}: {v}")
        req_lines.append("")
        req_lines.append(body)
        req = "\r\n".join(req_lines).encode("utf-8")
        conn.sendall(req)
        # Inform the server we're done sending (helps some servers/proxies to flush)
        try:
            try:
                conn.shutdown(socket.SHUT_WR)
            except Exception:
                # sometimes shutdown isn't supported on wrapped sockets; ignore
                pass
        except Exception:
            pass

        # read response until EOF or timeout. Use a slightly longer read window to be robust
        chunks = []
        # allow slightly longer total read timeout than the connect timeout
        read_deadline = time.time() + max(2, timeout)
        conn.settimeout(0.5)
        while True:
            try:
                data_r = conn.recv(4096)
            except socket.timeout:
                # if we've passed the read_deadline, stop; else retry
                if time.time() > read_deadline:
                    break
                else:
                    continue
            except Exception:
                break
            if not data_r:
                break
            chunks.append(data_r)
        raw = b"".join(chunks).decode("utf-8", errors="replace")
        parts = raw.split('\r\n\r\n', 1)
        headers_raw = parts[0] if parts else raw
        body_raw = parts[1] if len(parts) > 1 else ""
        first_line = headers_raw.splitlines()[0] if headers_raw.splitlines() else ""
        status_code = None
        try:
            status_code = int(first_line.split()[1])
        except Exception:
            status_code = None
        header_lines = headers_raw.splitlines()[1:]
        res_headers: Dict[str, List[str]] = {}
        for hline in header_lines:
            if ":" in hline:
                k, v = hline.split(":", 1)
                res_headers.setdefault(k.strip(), []).append(v.strip())

        result = {"returncode": 0, "status_code": status_code, "headers": res_headers, "body": body_raw, "raw": raw}
        if tls_info:
            result["tls"] = tls_info
        return result
    except Exception as e:
        return {"returncode": 1, "error": "send_recv_failed", "exception": str(e), "tls": tls_info}
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def load_config() -> None:
    """Load username/password from CONFIG_FILE. If missing, write a sample into OUTPUT_DIR and exit."""
    global USERNAME, PASSWORD
    if not CONFIG_FILE.exists():
        sample = {"username": "your_username_here", "password": "your_password_here"}
        sample_path = OUTPUT_DIR / "diagnose_mtls_and_cookies.config.sample"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        sample_path.write_text(json.dumps(sample, indent=2), encoding="utf-8")
        print(f"Config file not found: {CONFIG_FILE}\nA sample config has been written to: {sample_path}\nPlease create the config file with your credentials and rerun the script.")
        sys.exit(1)
    try:
        txt = CONFIG_FILE.read_text(encoding="utf-8")
        obj = json.loads(txt)
        USERNAME = obj.get("username", "")
        PASSWORD = obj.get("password", "")
        if not USERNAME or not PASSWORD:
            print(f"Config file {CONFIG_FILE} missing 'username' or 'password' fields.")
            sys.exit(1)
    except Exception as e:
        print(f"Failed to read/parse config {CONFIG_FILE}: {e}")
        sys.exit(1)


def openssl_probe(target_host: str, target_ip: str, port: int) -> Dict:
    """Use openssl s_client to probe TLS handshake and detect if server requests client certs.

    We use -servername to set SNI to target_host and -connect to target_ip:port.
    Look for strings like "REQUEST CERTIFICATE" or "Verify return code" or TLS alerts.
    """
    outname = f"{target_host}__openssl_{port}.txt"
    cmd = [OPENSSL_BIN, "s_client", "-connect", f"{target_ip}:{port}", "-servername", target_host, "-brief"]
    # not all openssl versions support -brief; we'll fallback to a verbose probe
    r = run_subprocess(cmd, timeout=2)
    if r["returncode"] != 0:
        # try without -brief
        cmd = [OPENSSL_BIN, "s_client", "-connect", f"{target_ip}:{port}", "-servername", target_host]
        r = run_subprocess(cmd, timeout=2)
    # Ensure stdout/stderr are strings (some subprocess wrappers may return bytes)
    stdout_txt = r.get("stdout", "")
    stderr_txt = r.get("stderr", "")
    if isinstance(stdout_txt, (bytes, bytearray)):
        stdout_txt = stdout_txt.decode("utf-8", errors="replace")
    if isinstance(stderr_txt, (bytes, bytearray)):
        stderr_txt = stderr_txt.decode("utf-8", errors="replace")
    out = "=== CMD: {}\n\n".format(" ".join(cmd)) + stdout_txt + "\n\nSTDERR:\n" + stderr_txt
    save(outname, out)

    # quick heuristics
    stderr = stderr_txt + stdout_txt
    requires_client_cert = False
    if "tlsv13 alert certificate required" in stderr.lower() or "certificate required" in stderr.lower():
        requires_client_cert = True
    if "request certificate" in stderr.lower() or "verify return code" in stderr.lower():
        # may be present even if no client cert required
        pass
    return {"target": target_host, "openssl_returncode": r.get("returncode"), "requires_client_cert": requires_client_cert, "out_file": outname}


def python_probe_login(target: dict, use_resolve: bool, follow_redirects: bool = True) -> Dict:
    """Use a pure-Python socket+ssl POST to the target. If use_resolve is True, connect to ip but set SNI to host.
    Otherwise connect normally to host (letting DNS resolve) and set SNI to host.
    Saves output to a file and returns a summary dict similar to the curl-based function.
    """
    host = target["host"]
    ip = target["ip"]
    port = target.get("port", 443)
    name = target.get("name")
    use_tls = (port == 443)
    connect_ip = ip if use_resolve else host
    outname = f"{name}__pyhttp_{'resolve' if use_resolve else 'nresolve'}_{port}.txt"
    data = f"username={USERNAME}&password={PASSWORD}"
    # Try multiple SNI names to detect vhost differences (host, IP, localhost)
    sni_candidates = [host, connect_ip, "localhost"]
    results = []
    found_set_cookie_any = False
    for sni in sni_candidates:
        sni_outname = outname.replace('.txt', f'.sni_{sni}.txt')
        r = raw_http_request_via_socket(host_for_sni=sni, connect_ip=connect_ip, port=port, path=LOGIN_PATH, method="POST", data=data, use_tls=use_tls)
        rawtxt = json.dumps(r, indent=2)
        save(sni_outname, rawtxt)
        headers = r.get("headers") or {}
        found_set_cookie = any(k.lower() == "set-cookie" for k in headers.keys()) if isinstance(headers, dict) else False
        if found_set_cookie:
            found_set_cookie_any = True
        results.append({"sni": sni, "returncode": r.get("returncode"), "found_set_cookie": found_set_cookie, "out_file": sni_outname})

    # Save summary for this probe
    save(outname, json.dumps(results, indent=2))
    return {"target": name, "use_resolve": use_resolve, "returncode": 0, "found_set_cookie": found_set_cookie_any, "out_file": outname, "per_sni": results}


def requests_login_probe(target: dict) -> Dict:
    """Use Python requests to attempt a login and capture response headers and session cookies."""
    try:
        import requests
    except Exception as e:
        return {"error": "requests_not_installed", "exception": str(e)}
    host = target["host"]
    ip = target["ip"]
    port = target.get("port", 443)
    name = target.get("name")
    # choose scheme: if port==443 use https, else use http
    scheme = "https" if port == 443 else "http"
    # if ip is provided and we want to mimic the browser hostname we use host in URL; requests will use host as SNI by default
    url = f"{scheme}://{host}:{port}{LOGIN_PATH}" if port not in (80, 443) else f"{scheme}://{host}{LOGIN_PATH}"

    s = requests.Session()
    headers_browser = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
    }
    # 1) GET the login page first to capture cookies and any CSRF token
    try:
        r_get = s.get(url, headers=headers_browser, verify=False, timeout=5)
    except Exception as e:
        return {"target": name, "error": "get_request_failed", "exception": str(e)}
    get_outname = f"{name}__requests_get_{port}.html"
    save(get_outname, r_get.text[:20000])

    # Try to find a CSRF token in the returned HTML (common patterns: name contains 'csrf')
    csrf_name = None
    csrf_value = None
    try:
        import re
        m = re.search(r"<input[^>]+name=[\'\"]?([^\'\"\s>]*csrf[^\'\"\s>]*)[\'\"]?[^>]*value=[\'\"]([^\'\"]+)[\'\"]", r_get.text, re.I)
        if m:
            csrf_name = m.group(1)
            csrf_value = m.group(2)
    except Exception:
        pass

    post_data = {"username": USERNAME, "password": PASSWORD}
    if csrf_name and csrf_value:
        post_data[csrf_name] = csrf_value

    try:
        r = s.post(url, headers=headers_browser, data=post_data, verify=False, timeout=5)
    except Exception as e:
        return {"target": name, "error": "post_request_failed", "exception": str(e)}
    headers_resp = dict(r.headers)
    cookies = {c.name: c.value for c in s.cookies}
    # Save response body and headers
    outname = f"{name}__requests_{port}.json"
    save(outname, json.dumps({"status_code": r.status_code, "headers": headers_resp, "cookies": cookies, "text_snippet": r.text[:2000]}, indent=2))
    return {"target": name, "status_code": r.status_code, "headers": headers_resp, "cookies": cookies, "out_file": outname, "get_out_file": get_outname}


def curl_login_probe(target: dict) -> Dict:
    """Use curl to POST login form with --resolve so SNI/Host match exactly and capture headers/body."""
    host = target["host"]
    ip = target["ip"]
    port = target.get("port", 443)
    name = target.get("name")
    url = f"https://{host}:{port}{LOGIN_PATH}" if port not in (80, 443) else f"https://{host}{LOGIN_PATH}"
    headers_file = OUTPUT_DIR / f"{name}__curl_post_{port}.headers"
    body_file = OUTPUT_DIR / f"{name}__curl_post_{port}.body"
    cmd = [
        "curl", "-k", "-v", "--http1.1", "--resolve", f"{host}:{port}:{ip}", url,
        "-X", "POST",
        "-d", f"username={USERNAME}&password={PASSWORD}",
        "-H", "Content-Type: application/x-www-form-urlencoded",
        "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--max-time", "2",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        # curl verbose goes to stderr; save both
        save(f"{name}__curl_post_{port}.stderr.txt", p.stderr)
        save(f"{name}__curl_post_{port}.stdout.txt", p.stdout)
        # try to extract HTTP status line from stderr
        status = None
        for line in (p.stderr or "").splitlines():
            if line.startswith('< HTTP/'):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        status = int(parts[1])
                    except Exception:
                        status = None
                    break
        return {"target": name, "curl_returncode": p.returncode, "status_code": status, "stderr_file": f"{name}__curl_post_{port}.stderr.txt", "stdout_file": f"{name}__curl_post_{port}.stdout.txt"}
    except Exception as e:
        return {"target": name, "error": "curl_failed", "exception": str(e)}


def curl_ignore_scope_probe(target: dict) -> Dict:
    """Use curl to emulate browser: GET login to collect cookies, then POST to /ignore/scope using the same cookie jar and --resolve."""
    host = target["host"]
    ip = target["ip"]
    port = target.get("port", 443)
    name = target.get("name")
    base = f"https://{host}:{port}" if port not in (80, 443) else f"https://{host}"
    login_url = base + LOGIN_PATH
    ignore_url = base + "/ignore/scope"
    jar = OUTPUT_DIR / f"{name}_cookies.jar"
    get_stderr = OUTPUT_DIR / f"{name}__curl_get_login.stderr.txt"
    post_stderr = OUTPUT_DIR / f"{name}__curl_post_ignore.stderr.txt"
    # GET login and save cookies
    cmd_get = ["curl", "-k", "-v", "--resolve", f"{host}:{port}:{ip}", login_url, "-c", str(jar), "--max-time", "2"]
    try:
        g = subprocess.run(cmd_get, capture_output=True, text=True, timeout=5)
        save(get_stderr.name, g.stderr)
    except Exception as e:
        return {"target": name, "error": "curl_get_failed", "exception": str(e)}
    # Try to find a CSRF token in the GET stderr/stdout (simple heuristics)
    csrf = None
    try:
        # read stdout from the get run if present
        out = g.stdout or ''
        import re
        m = re.search(r"name=[\'\"]?([^\'\"\s>]*csrf[^\'\"\s>]*)[\'\"]?[^>]*value=[\'\"]([^\'\"]+)[\'\"]", out, re.I)
        if m:
            csrf = (m.group(1), m.group(2))
    except Exception:
        csrf = None
    # POST to /ignore/scope with cookie jar
    post_cmd = [
        "curl", "-k", "-v", "--resolve", f"{host}:{port}:{ip}", ignore_url,
        "-b", str(jar), "-c", str(jar),
        "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "-H", "Content-Type: application/x-www-form-urlencoded",
        "--max-time", "2",
        "-d", "scope=site",
    ]
    if csrf:
        # append csrf field
        post_cmd[-1] = post_cmd[-1] + f"&{csrf[0]}={csrf[1]}"
    try:
        p = subprocess.run(post_cmd, capture_output=True, text=True, timeout=5)
        save(post_stderr.name, p.stderr)
        save(f"{name}__curl_post_ignore.stdout.txt", p.stdout)
        status = None
        for line in (p.stderr or "").splitlines():
            if line.startswith('< HTTP/'):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        status = int(parts[1])
                    except Exception:
                        status = None
                    break
        return {"target": name, "curl_post_ignore_returncode": p.returncode, "status_code": status, "stderr_file": post_stderr.name}
    except Exception as e:
        return {"target": name, "error": "curl_post_ignore_failed", "exception": str(e)}


def curl_full_login_and_ignore(target: dict) -> Dict:
    """Use curl to perform a full login (POST username/password) then POST /ignore/scope with that session.

    This avoids Python-requests differences and more closely mimics browser behavior.
    """
    host = target["host"]
    ip = target["ip"]
    port = target.get("port", 443)
    name = target.get("name")
    base = f"https://{host}:{port}" if port not in (80, 443) else f"https://{host}"
    login_url = base + LOGIN_PATH
    ignore_url = base + "/ignore/scope"
    jar = OUTPUT_DIR / f"{name}_full_cookies.jar"
    get_hdr = OUTPUT_DIR / f"{name}__curl_full_get.stderr.txt"
    login_hdr = OUTPUT_DIR / f"{name}__curl_full_login.stderr.txt"
    ignore_hdr = OUTPUT_DIR / f"{name}__curl_full_ignore.stderr.txt"

    # 1) GET login page to collect any initial cookies and possibly CSRF
    cmd_get = ["curl", "-k", "-v", "--resolve", f"{host}:{port}:{ip}", login_url, "-c", str(jar), "--max-time", "5"]
    try:
        g = subprocess.run(cmd_get, capture_output=True, text=True, timeout=8)
        save(get_hdr.name, g.stderr)
    except Exception as e:
        return {"target": name, "error": "curl_get_failed", "exception": str(e)}

    # 2) POST login with credentials from config
    login_cmd = [
        "curl", "-k", "-v", "--resolve", f"{host}:{port}:{ip}", login_url,
        "-b", str(jar), "-c", str(jar), "-L",
        "-H", "Content-Type: application/x-www-form-urlencoded",
        "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--max-time", "5",
        "-d", f"username={USERNAME}&password={PASSWORD}",
    ]
    try:
        p = subprocess.run(login_cmd, capture_output=True, text=True, timeout=10)
        save(login_hdr.name, p.stderr)
        save(f"{name}__curl_full_login.stdout.txt", p.stdout)
    except Exception as e:
        return {"target": name, "error": "curl_login_failed", "exception": str(e)}

    # 3) POST to /ignore/scope using the cookie jar
    post_cmd = [
        "curl", "-k", "-v", "--resolve", f"{host}:{port}:{ip}", ignore_url,
        "-b", str(jar), "-c", str(jar),
        "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "-H", "Content-Type: application/x-www-form-urlencoded",
        "--max-time", "5",
        "-d", "scope=site",
    ]
    try:
        r = subprocess.run(post_cmd, capture_output=True, text=True, timeout=10)
        save(ignore_hdr.name, r.stderr)
        save(f"{name}__curl_full_ignore.stdout.txt", r.stdout)
        status = None
        for line in (r.stderr or "").splitlines():
            if line.startswith('< HTTP/'):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        status = int(parts[1])
                    except Exception:
                        status = None
                    break
        return {"target": name, "login_returncode": p.returncode, "ignore_returncode": r.returncode, "status_code": status, "login_hdr": login_hdr.name, "ignore_hdr": ignore_hdr.name}
    except Exception as e:
        return {"target": name, "error": "curl_post_ignore_failed", "exception": str(e)}


def summarize(results: List[Dict]) -> None:
    print("\n=== SUMMARY ===")
    for r in results:
        print(json.dumps(r, indent=2))
    print(f"\nOutputs written to: {OUTPUT_DIR}\n")


def main():
    results = []
    load_config()
    print("Diagnostic run started. Targets:")
    for t in TARGETS:
        print(f" - {t['name']}: host={t['host']} ip={t['ip']} port={t.get('port',443)}")
    print("\nMake sure you edit USERNAME/PASSWORD and TARGETS in this file before running.")

    for t in TARGETS:
        name = t.get("name")
        host = t.get("host")
        ip = t.get("ip")
        port = t.get("port", 443)
        print(f"\n--- Target: {name} (host={host} ip={ip} port={port}) ---")
        # 1) openssl probe
        if shutil.which(OPENSSL_BIN):
            print("Running openssl s_client probe...")
            osres = openssl_probe(host, ip, port)
            print(f"  openssl_requires_client_cert: {osres.get('requires_client_cert')} (output: {osres.get('out_file')})")
            results.append(osres)
        else:
            print("openssl not found; skipping openssl probe")
            results.append({"target": name, "openssl_probe": "skipped"})

        # 2) python probe: direct (no resolve) — may use IP in URL which sets SNI differently
        print("Running python HTTP probe (no resolve)...")
        c1 = python_probe_login(t, use_resolve=False, follow_redirects=True)
        print(f"  pyhttp(no-resolve) found_set_cookie: {c1.get('found_set_cookie')} out: {c1.get('out_file')}")
        results.append(c1)

        # 3) python probe: with resolve to force SNI==host but connect to ip
        print("Running python HTTP probe (with resolve) to force SNI/Host mapping...")
        c2 = python_probe_login(t, use_resolve=True, follow_redirects=True)
        print(f"  pyhttp(resolve) found_set_cookie: {c2.get('found_set_cookie')} out: {c2.get('out_file')}")
        results.append(c2)

        # 4) requests probe
        print("Running Python requests login (verify=False)...")
        rq = requests_login_probe(t)
        if "error" in rq:
            print(f"  requests error: {rq}")
        else:
            print(f"  requests status: {rq.get('status_code')} cookies_saved_in_session: {bool(rq.get('cookies'))} out: {rq.get('out_file')}")
        results.append(rq)

    # 5) curl POST probe (browser-like)
    print("Running curl POST probe (browser-like headers)...")
    cr = curl_login_probe(t)
    print(f"  curl post: {cr}")
    results.append(cr)

    # 6) curl flow to POST to /ignore/scope using cookie jar and optional CSRF
    print("Running curl flow to POST /ignore/scope (using cookie jar)...")
    ci = curl_ignore_scope_probe(t)
    print(f"  curl ignore/scope: {ci}")
    results.append(ci)

    summarize(results)


if __name__ == "__main__":
    main()
