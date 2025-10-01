#!/usr/bin/env python3
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__) + '/..')

from datetime import datetime, timezone
from app.db import async_session
from app.models import User, CompletedOccurrence
from sqlmodel import select
import uuid

async def quick_test():
    async with async_session() as sess:
        username = f'phase2_quick_{uuid.uuid4().hex[:8]}'
        user = User(username=username, password_hash='test')
        sess.add(user)
        await sess.commit()
        await sess.refresh(user)
        
        occ_dt = datetime(2025, 10, 20, 14, 30, 0, tzinfo=timezone.utc)
        
        comp = CompletedOccurrence(
            user_id=user.id,
            occ_hash=None,
            item_type='todo',
            item_id=999,
            occurrence_dt=occ_dt,
        )
        sess.add(comp)
        await sess.commit()
        await sess.refresh(comp)
        
        print(f'✅ Created completion with NULL hash: id={comp.id}')
        print(f'   Hash: {comp.occ_hash}')
        print(f'   Metadata: {comp.item_type}:{comp.item_id}:{comp.occurrence_dt}')
        
        await sess.delete(comp)
        await sess.delete(user)
        await sess.commit()

asyncio.run(quick_test())
