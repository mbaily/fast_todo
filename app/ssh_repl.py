"""AsyncSSH-powered SSH server exposing the app REPL.

Users connect over SSH and get a line-oriented REPL which executes code via
repl_api.run_code_for_user. Authentication supports:
- Password: verify against app auth (username/password)
- Public key: match against SshPublicKey table where enabled=True

Configure via env:
- SSH_REPL_ENABLE=1 to start
- SSH_REPL_BIND=0.0.0.0  SSH_REPL_PORT=2222
- SSH_REPL_HOST_KEY_PATH=./ssh_repl_host_key
"""
from __future__ import annotations

import asyncio
import os
import asyncssh
from typing import Optional

from .auth import get_user_by_username, verify_password
import logging
logger = logging.getLogger(__name__)
try:
    fh = logging.FileHandler('debug_ssh_repl.log')
    fh.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s %(levelname)s:%(name)s: %(message)s')
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info('SSH REPL: debug logger initialized')
except Exception:
    # If file handler can't be created (permissions), continue with default logger
    pass
from .models import SshPublicKey
from .db import async_session
from sqlmodel import select
from .repl_api import run_code_for_user


class ReplSSHServer(asyncssh.SSHServer):
    def __init__(self):
        self._username: Optional[str] = None

    def connection_made(self, conn):
        self._conn = conn
        try:
            peer = conn.get_extra_info('peername')
        except Exception:
            peer = None
        logger.info('SSH REPL: connection made from %s', peer)

    def begin_auth(self, username: str):
        self._username = username
        logger.info('SSH REPL: begin_auth for username=%s', username)
        return True  # request auth methods

    def password_auth_supported(self):
        logger.info('SSH REPL: password_auth_supported called')
        return True

    def public_key_auth_supported(self):
        logger.info('SSH REPL: public_key_auth_supported called')
        return True

    # Older/newer AsyncSSH versions may call alternate wrapper names
    # Provide a non-async wrapper that delegates to async validate_password
    def validate_auth_password(self, username: str, password: str):
        logger.info('SSH REPL: validate_auth_password wrapper called for username=%s', username)
        try:
            # schedule and wait on the async validator
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self.validate_password(username, password))
        except Exception as e:
            logger.exception('SSH REPL: validate_auth_password wrapper error for %s: %s', username, e)
            return False
    def session_requested(self):
        # Called after auth; provide a session bound to the authenticated username
        return ReplSSHSession(self._username or 'unknown')

    async def validate_password(self, username: str, password: str) -> bool:
        logger.info('SSH REPL: validate_password for username=%s', username)
        user = await get_user_by_username(username)
        if not user:
            logger.info('SSH REPL: user not found for password auth username=%s', username)
            return False
        ok = await verify_password(password, user.password_hash)
        logger.info('SSH REPL: password auth %s for username=%s', 'OK' if ok else 'FAIL', username)
        return bool(ok)

    async def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        try:
            fp = key.get_fingerprint()
        except Exception:
            fp = 'unknown'
        logger.info('SSH REPL: validate_public_key for username=%s fingerprint=%s', username, fp)
        # Compare the raw public key serialization with DB entries
        async with async_session() as sess:
            q = await sess.exec(select(SshPublicKey).where(SshPublicKey.enabled == True))
            rows = q.all()
            for row in rows:
                try:
                    db_key = asyncssh.public_key_from_string(row.public_key)
                    if db_key == key:
                        # Optionally tie to username by user_id mapping
                        # Fetch user and ensure ownership if desired
                        u = await get_user_by_username(username)
                        if not u:
                            logger.info('SSH REPL: key match but user not found username=%s', username)
                            return False
                        if row.user_id != u.id:
                            logger.info('SSH REPL: key fingerprint matched but owned by different user_id row_user_id=%s username=%s', row.user_id, username)
                            continue
                        logger.info('SSH REPL: public key auth OK for username=%s fingerprint=%s', username, fp)
                        return True
                except Exception:
                    continue
        logger.info('SSH REPL: public key auth FAIL for username=%s fingerprint=%s', username, fp)
        return False


class ReplSSHSession(asyncssh.SSHServerSession):
    def __init__(self, username: str):
        self.username = username
        self._chan = None
        self._inp = b''
        # Create a persistent REPL instance for this session so cd/pwd persist
        try:
            # lazy import and create Repl bound to the user after auth
            self._repl = None
        except Exception:
            self._repl = None
        # persistent locals for this session so variable assignments stick
        self._locals = {}

    def connection_made(self, chan):
        self._chan = chan
        try:
            peer = chan.get_extra_info('peername')
        except Exception:
            peer = None
        logger.info('SSH REPL: session started for username=%s from %s', self.username, peer)

    def session_started(self):
        self._chan.write("Welcome to gpt5_fast_todo REPL over SSH\n")
        self._chan.write("Type help() or Ctrl-D to exit.\n\n>>> ")

    async def shell_requested(self):
        return True

    def data_received(self, data, datatype):
        self._inp += data.encode() if isinstance(data, str) else data
        while b"\n" in self._inp:
            line, self._inp = self._inp.split(b"\n", 1)
            cmd = line.decode(errors='ignore').strip()
            logger.info('SSH REPL: cmd username=%s text=%s', self.username, cmd[:200])
            if cmd in ("exit", "quit"):
                self._chan.write("Bye.\n")
                self._chan.exit(0)
                return
            asyncio.create_task(self._run_cmd(cmd))

    async def _run_cmd(self, code: str):
        try:
            user = await get_user_by_username(self.username)
            # ensure we have a session-scoped Repl bound to this user
            if self._repl is None:
                try:
                    from .repl_api import Repl
                    self._repl = Repl(user)
                except Exception:
                    self._repl = None

            out, val = await asyncio.get_running_loop().run_in_executor(None, run_code_for_user, user, code, self._repl, self._locals)
            if out:
                # out is captured stdout from run_code_for_user and may contain
                # raw newlines; write it as-is so clients see correct formatting.
                self._chan.write(out if out.endswith("\n") else out + "\n")
            if val is not None:
                # If the last value is a plain string, write it raw to preserve
                # newlines instead of JSON-escaping them. For other types, use
                # JSON for structured display, falling back to str().
                try:
                    if isinstance(val, str):
                        self._chan.write(val if val.endswith("\n") else val + "\n")
                    else:
                        import json
                        self._chan.write(json.dumps(val, default=str) + "\n")
                except Exception:
                    self._chan.write(str(val) + "\n")
        except Exception as e:
            logger.exception('SSH REPL: error running code for username=%s', self.username)
            self._chan.write(f"Error: {e}\n")
        finally:
            self._chan.write(">>> ")


async def _handle_client(process):
    pass  # unused; we use SSHServerSession


async def start_server() -> asyncssh.SSHServer:  # returns server object
    bind = os.getenv('SSH_REPL_BIND', '0.0.0.0')
    port = int(os.getenv('SSH_REPL_PORT', '2222'))
    host_key_path = os.getenv('SSH_REPL_HOST_KEY_PATH', './ssh_repl_host_key')
    # ensure host key exists and is valid; regenerate if invalid
    def _generate_host_key(path: str):
        key = asyncssh.generate_private_key('ssh-ed25519')
        content = key.export_private_key()
        if isinstance(content, str):
            data = content.encode('utf-8')
        else:
            data = content
        with open(path, 'wb') as f:
            f.write(data)
        os.chmod(path, 0o600)
        logger.info('SSH REPL: generated new host key at %s', path)

    if not os.path.exists(host_key_path) or os.path.getsize(host_key_path) < 64:
        logger.warning('SSH REPL: host key missing or too small at %s; generating new', host_key_path)
        _generate_host_key(host_key_path)
    else:
        try:
            # Validate by attempting to read the key
            asyncssh.read_private_key(host_key_path)
            logger.info('SSH REPL: using existing host key at %s', host_key_path)
        except Exception as e:
            logger.warning('SSH REPL: invalid host key at %s (%s); regenerating', host_key_path, e)
            _generate_host_key(host_key_path)
    return await asyncssh.create_server(
        lambda: ReplSSHServer(),
        bind, port,
        server_host_keys=[host_key_path],
    )
