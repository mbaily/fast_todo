#!/usr/bin/env python3
"""
Test that calendar shows completed occurrences after title change.
"""
import asyncio
import os
import pytest

# Ensure pytest treats this async test correctly
pytestmark = pytest.mark.asyncio
from datetime import datetime, timezone, timedelta


async def _async_test_calendar_shows_completed_after_title_change():
    """Test that calendar endpoint returns completed occurrences after title change."""
    os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///./fast_todo.db')
    
    from sqlmodel import select
    from app.db import async_session, init_db
    from app.models import User, ListState, Todo, CompletedOccurrence
    from app.utils import occurrence_hash
    from app.main import app
    from fastapi.testclient import TestClient
    
    await init_db()
    
    test_date = datetime(2025, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    
    async with async_session() as sess:
        # Get test user
        result = await sess.exec(select(User).limit(1))
        user = result.first()
        if not user:
            print("❌ No user found in database")
            return False
        
        print(f"✓ Using user: {user.username}")
        
        # Get test list
        result = await sess.exec(select(ListState).where(
            ListState.owner_id == user.id
        ).limit(1))
        test_list = result.first()
        if not test_list:
            print("❌ No list found")
            return False
        
        # Create test todo with a date
        original_text = 'Dentist appointment on October 20 2025'
        todo = Todo(
            text=original_text,
            list_id=test_list.id,
        )
        sess.add(todo)
        await sess.commit()
        await sess.refresh(todo)
        
        print(f"✓ Created todo #{todo.id}: '{todo.text}'")
        
        # Generate hash with original title
        hash1 = occurrence_hash('todo', todo.id, test_date, '', todo.text)
        print(f"✓ Original hash: {hash1[:30]}...")
        
        # Mark it complete WITH metadata
        comp = CompletedOccurrence(
            user_id=user.id,
            occ_hash=hash1,
            item_type='todo',
            item_id=todo.id,
            occurrence_dt=test_date
        )
        sess.add(comp)
        await sess.commit()
        print(f"✓ Marked complete with metadata")
        
        # Query calendar endpoint to see if occurrence shows up as completed
        # We'll do this programmatically by calling the calendar logic
        from app.main import calendar_occurrences
        from fastapi import Request
        from unittest.mock import Mock
        
        # Mock request
        mock_request = Mock(spec=Request)
        mock_request.headers = {}
        
        # Query calendar with window around our test date
        start = (test_date - timedelta(days=1)).isoformat()
        end = (test_date + timedelta(days=1)).isoformat()
        
        # We need to call this with the user context
        class MockUser:
            id = user.id
        
        result = await calendar_occurrences(
            mock_request,
            start=start,
            end=end,
            current_user=MockUser()
        )
        
        occs = result.get('occurrences', [])
        our_occ = [o for o in occs if o.get('id') == todo.id]
        
        if not our_occ:
            print(f"❌ Occurrence not found in calendar (expected 1)")
            print(f"   Total occurrences: {len(occs)}")
            return False
        
        print(f"✓ Found occurrence in calendar")
        
        if our_occ[0].get('completed'):
            print(f"✓ Occurrence is marked as completed (correct!)")
        else:
            print(f"❌ Occurrence is NOT marked as completed (incorrect)")
            print(f"   Occurrence data: {our_occ[0]}")
            return False
        
        # Now change the title
        new_text = 'Dental checkup on October 20 2025'
        todo.text = new_text
        sess.add(todo)
        await sess.commit()
        print(f"✓ Changed todo text to: '{todo.text}'")
        
        # Generate new hash
        hash2 = occurrence_hash('todo', todo.id, test_date, '', todo.text)
        print(f"✓ New hash: {hash2[:30]}...")
        
        if hash1 == hash2:
            print(f"⚠️  Hashes are the same (unexpected)")
        else:
            print(f"✓ Hash changed as expected")
        
        # Query calendar again
        result2 = await calendar_occurrences(
            mock_request,
            start=start,
            end=end,
            current_user=MockUser()
        )
        
        occs2 = result2.get('occurrences', [])
        our_occ2 = [o for o in occs2 if o.get('id') == todo.id]
        
        if not our_occ2:
            print(f"❌ Occurrence not found after title change")
            return False
        
        print(f"✓ Found occurrence after title change")
        
        if our_occ2[0].get('completed'):
            print(f"✅ SUCCESS: Occurrence STILL marked as completed after title change!")
            print(f"   This means the metadata fallback is working!")
        else:
            print(f"❌ FAILURE: Occurrence is no longer marked as completed")
            print(f"   The metadata fallback is NOT working")
            print(f"   Occurrence data: {our_occ2[0]}")
            
            # Debug: check if metadata exists
            result = await sess.exec(select(CompletedOccurrence).where(
                CompletedOccurrence.user_id == user.id,
                CompletedOccurrence.item_id == todo.id
            ))
            comp_check = result.first()
            if comp_check:
                print(f"   Completion record exists:")
                print(f"     - item_type: {comp_check.item_type}")
                print(f"     - item_id: {comp_check.item_id}")
                print(f"     - occurrence_dt: {comp_check.occurrence_dt}")
            return False
        
        # Cleanup
        await sess.delete(comp)
        await sess.delete(todo)
        await sess.commit()
        print(f"✓ Cleanup complete")
        
        return True


async def main():
    print("=" * 70)
    print("Testing calendar shows completed occurrences after title change")
    print("=" * 70)
    print()
    
    success = await _async_test_calendar_shows_completed_after_title_change()
    
    print()
    print("=" * 70)
    if success:
        print("✅ TEST PASSED")
    else:
        print("❌ TEST FAILED")
    print("=" * 70)


def test_calendar_shows_completed_after_title_change():
    asyncio.run(_async_test_calendar_shows_completed_after_title_change())

if __name__ == '__main__':
    asyncio.run(main())
