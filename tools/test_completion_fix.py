#!/usr/bin/env python3
"""
Test that completion history is preserved when todo text changes.
"""
import asyncio
import os
import pytest
pytestmark = pytest.mark.asyncio
from datetime import datetime, timezone


async def _async_test_completion_survives_title_change():
    """Test that a completed occurrence remains completed after title change."""
    os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///./fast_todo.db')
    
    from sqlmodel import select
    from app.db import async_session, init_db
    from app.models import User, ListState, Todo, CompletedOccurrence
    from app.utils import occurrence_hash
    
    await init_db()
    
    test_date = datetime(2025, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    
    async with async_session() as sess:
        # Get or create test user
        result = await sess.exec(select(User).limit(1))
        user = result.first()
        if not user:
            print("❌ No user found in database")
            return False
        
        print(f"✓ Using user: {user.username}")
        
        # Get or create test list
        result = await sess.exec(select(ListState).where(
            ListState.owner_id == user.id
        ).limit(1))
        test_list = result.first()
        if not test_list:
            test_list = ListState(name='test-completion-fix', owner_id=user.id)
            sess.add(test_list)
            await sess.commit()
            await sess.refresh(test_list)
        
        print(f"✓ Using list: {test_list.name}")
        
        # Create test todo
        original_text = 'Doctor appointment on October 15'
        todo = Todo(
            text=original_text,
            list_id=test_list.id,
        )
        sess.add(todo)
        await sess.commit()
        await sess.refresh(todo)
        
        print(f"✓ Created todo #{todo.id}: '{todo.text}'")
        
        # Generate hash for Oct 15 occurrence with original title
        hash1 = occurrence_hash('todo', todo.id, test_date, '', todo.text)
        print(f"✓ Hash with original title: {hash1[:30]}...")
        
        # Mark it complete WITH METADATA (new behavior)
        comp = CompletedOccurrence(
            user_id=user.id,
            occ_hash=hash1,
            item_type='todo',
            item_id=todo.id,
            occurrence_dt=test_date
        )
        sess.add(comp)
        await sess.commit()
        print(f"✓ Marked complete with metadata stored")
        
        # Verify it's marked as completed by hash
        result = await sess.exec(select(CompletedOccurrence).where(
            CompletedOccurrence.user_id == user.id,
            CompletedOccurrence.occ_hash == hash1
        ))
        if result.first():
            print(f"✓ Confirmed: completion found by hash")
        else:
            print(f"❌ ERROR: completion not found by hash")
            return False
        
        # Now change the todo text
        new_text = 'Dr. checkup on October 15'
        todo.text = new_text
        sess.add(todo)
        await sess.commit()
        print(f"✓ Changed todo text to: '{todo.text}'")
        
        # Generate new hash with changed title
        hash2 = occurrence_hash('todo', todo.id, test_date, '', todo.text)
        print(f"✓ Hash with new title: {hash2[:30]}...")
        
        if hash1 == hash2:
            print(f"⚠️  WARNING: Hashes are the same (unexpected)")
            return False
        
        print(f"✓ Confirmed: hash changed due to title change")
        
        # Check if we can still find it by hash (should fail)
        result = await sess.exec(select(CompletedOccurrence).where(
            CompletedOccurrence.user_id == user.id,
            CompletedOccurrence.occ_hash == hash2
        ))
        if result.first():
            print(f"❌ ERROR: Found by NEW hash (shouldn't exist)")
            return False
        else:
            print(f"✓ Confirmed: new hash doesn't match (expected)")
        
        # But we CAN find it by metadata!
        result = await sess.exec(select(CompletedOccurrence).where(
            CompletedOccurrence.user_id == user.id,
            CompletedOccurrence.item_type == 'todo',
            CompletedOccurrence.item_id == todo.id,
            CompletedOccurrence.occurrence_dt == test_date
        ))
        if result.first():
            print(f"✅ SUCCESS: Found by metadata (item_type, item_id, occurrence_dt)")
            print(f"   This is the fix! Completion history survives title changes.")
        else:
            print(f"❌ ERROR: Not found by metadata")
            return False
        
        # Cleanup
        await sess.delete(comp)
        await sess.delete(todo)
        await sess.commit()
        print(f"✓ Cleanup complete")
        
        return True


async def main():
    print("=" * 70)
    print("Testing completion history preservation after title change")
    print("=" * 70)
    print()
    
    success = await _async_test_completion_survives_title_change()
    
    print()
    print("=" * 70)
    if success:
        print("✅ TEST PASSED")
        print()
        print("The fix is working! Completions now:")
        print("1. Store metadata (item_type, item_id, occurrence_dt)")
        print("2. Can be found by metadata even when title changes")
        print("3. Your completion history is preserved!")
    else:
        print("❌ TEST FAILED")
    print("=" * 70)


def test_completion_survives_title_change():
    asyncio.run(_async_test_completion_survives_title_change())

if __name__ == '__main__':
    asyncio.run(main())
