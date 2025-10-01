#!/usr/bin/env python3
"""
Test the real issue: User changes the TIME in the todo text.
"""
import asyncio
import os
import pytest
pytestmark = pytest.mark.asyncio
from datetime import datetime, timezone


async def _async_test_time_change():
    os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///./fast_todo.db')
    
    from sqlmodel import select
    from app.db import async_session, init_db
    from app.models import User, ListState, Todo, CompletedOccurrence
    from app.utils import occurrence_hash, extract_dates
    
    await init_db()
    
    print("=" * 70)
    print("Testing: User changes TIME in todo text")
    print("=" * 70)
    print()
    
    async with async_session() as sess:
        # Get user and list
        result = await sess.exec(select(User).limit(1))
        user = result.first()
        result = await sess.exec(select(ListState).where(ListState.owner_id == user.id).limit(1))
        test_list = result.first()
        
        # Create todo WITHOUT specific time
        original_text = 'Team standup on October 22'
        todo = Todo(text=original_text, list_id=test_list.id)
        sess.add(todo)
        await sess.commit()
        await sess.refresh(todo)
        
        print(f"1. Original todo: '{todo.text}'")
        
        # Extract date
        dates = extract_dates(original_text)
        occ_dt_1 = dates[0] if dates else None
        print(f"   Extracted date: {occ_dt_1.isoformat()}")
        print(f"   Time: {occ_dt_1.strftime('%H:%M:%S')}")
        print()
        
        # Mark complete
        hash1 = occurrence_hash('todo', todo.id, occ_dt_1, '', todo.text)
        comp = CompletedOccurrence(
            user_id=user.id,
            occ_hash=hash1,
            item_type='todo',
            item_id=todo.id,
            occurrence_dt=occ_dt_1
        )
        sess.add(comp)
        await sess.commit()
        print(f"2. Marked complete with:")
        print(f"   occurrence_dt: {occ_dt_1.isoformat()}")
        print()
        
        # User edits to add a specific time
        new_text = 'Team standup at 10am on October 22'
        todo.text = new_text
        sess.add(todo)
        await sess.commit()
        
        print(f"3. User edits todo: '{todo.text}'")
        
        # Extract new date
        dates2 = extract_dates(new_text)
        occ_dt_2 = dates2[0] if dates2 else None
        print(f"   New extracted date: {occ_dt_2.isoformat()}")
        print(f"   Time: {occ_dt_2.strftime('%H:%M:%S')}")
        print()
        
        # Compare
        print(f"4. Comparison:")
        print(f"   Same date: {occ_dt_1.date() == occ_dt_2.date()}")
        print(f"   Same time: {occ_dt_1.time() == occ_dt_2.time()}")
        print(f"   Same full datetime: {occ_dt_1 == occ_dt_2}")
        print()
        
        if occ_dt_1 != occ_dt_2:
            print(f"   ⚠️  Datetimes differ!")
            print(f"   Original: {occ_dt_1.isoformat()}")
            print(f"   New:      {occ_dt_2.isoformat()}")
            print()
            print(f"   This is why we use DATE-ONLY comparison.")
            print(f"   User changed the TIME in the text, but it's still the")
            print(f"   same logical occurrence (same date).")
        
        # Cleanup
        await sess.delete(comp)
        await sess.delete(todo)
        await sess.commit()
        
        print()
        print("=" * 70)


def test_time_change():
    asyncio.run(_async_test_time_change())

if __name__ == '__main__':
    asyncio.run(_async_test_time_change())
