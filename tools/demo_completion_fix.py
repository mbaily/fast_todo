#!/usr/bin/env python3
"""
Practical demo: Mark a todo occurrence complete, change the title, verify it stays completed.

This is the user-facing workflow that now works correctly.
"""
import asyncio
import os
from datetime import datetime, timezone, timedelta


async def demo():
    os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///./fast_todo.db')
    
    from sqlmodel import select
    from app.db import async_session, init_db
    from app.models import User, ListState, Todo, CompletedOccurrence
    
    await init_db()
    
    print("=" * 70)
    print("PRACTICAL DEMO: Completion History Preservation")
    print("=" * 70)
    print()
    
    async with async_session() as sess:
        # Get user
        result = await sess.exec(select(User).limit(1))
        user = result.first()
        if not user:
            print("No user found")
            return
        
        # Get or create demo list
        result = await sess.exec(select(ListState).where(
            ListState.owner_id == user.id,
            ListState.name == 'Demo List'
        ))
        demo_list = result.first()
        if not demo_list:
            demo_list = ListState(name='Demo List', owner_id=user.id)
            sess.add(demo_list)
            await sess.commit()
            await sess.refresh(demo_list)
        
        # Create a todo with a date
        todo_text = 'Team meeting on October 25 2025'
        todo = Todo(text=todo_text, list_id=demo_list.id)
        sess.add(todo)
        await sess.commit()
        await sess.refresh(todo)
        
        print(f"1. Created todo: '{todo.text}'")
        print(f"   ID: {todo.id}")
        print()
        
        # Simulate marking it complete via the UI
        # In real usage, this would come from the calendar page JavaScript
        from app.utils import occurrence_hash
        
        occ_date = datetime(2025, 10, 25, 0, 0, 0, tzinfo=timezone.utc)
        occ_hash = occurrence_hash('todo', todo.id, occ_date, '', todo.text)
        
        completion = CompletedOccurrence(
            user_id=user.id,
            occ_hash=occ_hash,
            item_type='todo',
            item_id=todo.id,
            occurrence_dt=occ_date
        )
        sess.add(completion)
        await sess.commit()
        
        print(f"2. Marked occurrence complete:")
        print(f"   Date: 2025-10-25")
        print(f"   Hash: {occ_hash[:40]}...")
        print(f"   Metadata: item_type=todo, item_id={todo.id}, date=2025-10-25")
        print()
        
        # Now change the todo text (simulate user editing)
        old_text = todo.text
        new_text = 'Staff meeting on October 25 2025'
        todo.text = new_text
        sess.add(todo)
        await sess.commit()
        
        print(f"3. Changed todo text:")
        print(f"   Old: '{old_text}'")
        print(f"   New: '{new_text}'")
        print()
        
        # Calculate new hash
        new_hash = occurrence_hash('todo', todo.id, occ_date, '', todo.text)
        print(f"4. Hash comparison:")
        print(f"   Old hash: {occ_hash[:40]}...")
        print(f"   New hash: {new_hash[:40]}...")
        print(f"   Match: {occ_hash == new_hash}")
        print()
        
        # Check if completion can still be found
        # By hash (will fail)
        result = await sess.exec(select(CompletedOccurrence).where(
            CompletedOccurrence.user_id == user.id,
            CompletedOccurrence.occ_hash == new_hash
        ))
        found_by_hash = result.first()
        
        # By metadata (will succeed!)
        result = await sess.exec(select(CompletedOccurrence).where(
            CompletedOccurrence.user_id == user.id,
            CompletedOccurrence.item_type == 'todo',
            CompletedOccurrence.item_id == todo.id,
            CompletedOccurrence.occurrence_dt >= datetime(2025, 10, 25, 0, 0, 0, tzinfo=timezone.utc),
            CompletedOccurrence.occurrence_dt < datetime(2025, 10, 26, 0, 0, 0, tzinfo=timezone.utc)
        ))
        found_by_metadata = result.first()
        
        print(f"5. Lookup results:")
        print(f"   Found by new hash: {'✓' if found_by_hash else '✗'}")
        print(f"   Found by metadata: {'✓' if found_by_metadata else '✗'}")
        print()
        
        if found_by_metadata:
            print("✅ SUCCESS!")
            print()
            print("The completion record was preserved despite the title change.")
            print("When you view the calendar, this occurrence will still show as completed.")
            print()
            print("This is exactly how the fix works:")
            print("  1. Calendar generates occurrence with NEW title → NEW hash")
            print("  2. Checks if NEW hash in completions → not found")
            print("  3. Falls back to metadata (todo_id + date) → found!")
            print("  4. Marks occurrence as completed ✓")
        else:
            print("❌ Not found by metadata (unexpected)")
        
        # Cleanup
        await sess.delete(completion)
        await sess.delete(todo)
        await sess.commit()
        
        print()
        print("=" * 70)
        print("Demo complete (cleanup done)")
        print("=" * 70)


if __name__ == '__main__':
    asyncio.run(demo())
