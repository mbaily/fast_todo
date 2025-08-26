#!/usr/bin/env bash
# Simple admin helper to manage users from Debian/bash until a web UI exists.
# Usage: scripts/manage_users.sh create|delete|promote <username>

set -euo pipefail

cmd=${1:-}
user=${2:-}

if [[ -z "$cmd" || -z "$user" ]]; then
  echo "Usage: $0 create|delete|promote <username>"
  exit 2
fi
# If DATABASE_URL is not set, prefer a system-installed path when available
# so admin scripts don't create test.db in the repo root by accident.
if [ -z "${DATABASE_URL:-}" ]; then
    if [ -d "/opt/gpt5_fast_todo" ]; then
        export DATABASE_URL="sqlite+aiosqlite:///opt/gpt5_fast_todo/fast_todo.db"
    else
        export DATABASE_URL="sqlite+aiosqlite:///./fast_todo.db"
    fi
fi

python - "$cmd" "$user" <<PY
import sys
from getpass import getpass
from app.db import async_session, init_db
from app.models import User
from passlib.context import CryptContext
import asyncio

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def run():
    await init_db()
    async with async_session() as sess:
        if sys.argv[1] == 'create':
            uname = sys.argv[2]
            pw = getpass(f"Password for {uname}: ")
            ph = pwd_context.hash(pw)
            u = User(username=uname, password_hash=ph, is_admin=False)
            sess.add(u)
            try:
                await sess.commit()
                print('created')
            except Exception as e:
                await sess.rollback()
                print('error', e)
        elif sys.argv[1] == 'delete':
            uname = sys.argv[2]
            q = await sess.exec(User.__table__.select().where(User.username==uname))
            row = q.first()
            if not row:
                print('not found')
                return
            await sess.delete(row)
            await sess.commit()
            print('deleted')
        elif sys.argv[1] == 'promote':
            uname = sys.argv[2]
            q = await sess.exec(User.__table__.select().where(User.username==uname))
            row = q.first()
            if not row:
                print('not found')
                return
            row.is_admin = True
            sess.add(row)
            await sess.commit()
            print('promoted')
        else:
            print('unknown')

if __name__ == '__main__':
    asyncio.run(run())
PY
