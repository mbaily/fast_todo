"""Minimal deterministic test DB seeder.

Creates a small, deterministic `fast_todo.db` suitable for unit tests that
exercise yearless-date resolution without flooding the calendar with
recurring occurrences.

Usage:
  export DATABASE_URL="sqlite+aiosqlite:///./fast_todo.db"
  python3 tools/seed_minimal_test_db.py
"""
import asyncio
from app.db import init_db, async_session
from app.models import ListState, User

async def main():
    await init_db()
    from sqlmodel import select
    from app.auth import pwd_context
    async with async_session() as sess:
        # Create a small set of lists used by tests
        names = [
            'Integration YR List',
            'Integration Window List',
            'Minimal Seed List'
        ]
        for name in names:
            q = await sess.exec(select(ListState).where(ListState.name == name))
            existing = q.first()
            if not existing:
                ls = ListState(name=name)
                sess.add(ls)

        # Ensure test users exist
        q = await sess.exec(select(User).where(User.username == '__autotest__'))
        if not q.first():
            ph = pwd_context.hash('p')
            u = User(username='__autotest__', password_hash=ph, is_admin=True)
            sess.add(u)
        q = await sess.exec(select(User).where(User.username == 'testuser'))
        if not q.first():
            ph2 = pwd_context.hash('testpass')
            u2 = User(username='testuser', password_hash=ph2, is_admin=True)
            sess.add(u2)
        await sess.commit()

if __name__ == '__main__':
    asyncio.run(main())
