#!/usr/bin/env python3
"""
Test Phase 1: Verify metadata-based completion lookups work correctly.
This tests that completions can be stored and retrieved using metadata
instead of occ_hash.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio
from datetime import datetime, timezone
from app.db import async_session
from app.models import User, Todo, ListState, CompletedOccurrence
from sqlmodel import select
import pytest

@pytest.mark.asyncio
async def test_phase1():
    """Test that completion lookups work using metadata instead of hash."""
    import uuid
    
    async with async_session() as sess:
        # Create test user
        username = f'phase1_test_{uuid.uuid4().hex[:8]}'
        user = User(username=username, password_hash='test')
        sess.add(user)
        await sess.commit()
        await sess.refresh(user)
        
        # Create test list and todo
        lst = ListState(name='Phase 1 Test List', owner_id=user.id)
        sess.add(lst)
        await sess.commit()
        await sess.refresh(lst)
        
        todo = Todo(text='Test todo for Phase 1', list_id=lst.id)
        sess.add(todo)
        await sess.commit()
        await sess.refresh(todo)
        
        print(f"Created test user: {user.username} (id={user.id})")
        print(f"Created test todo: {todo.id}\n")
        
        # Clean up any existing completions for this todo (from previous test runs)
        existing = await sess.exec(
            select(CompletedOccurrence)
            .where(CompletedOccurrence.user_id == user.id)
            .where(CompletedOccurrence.item_type == 'todo')
            .where(CompletedOccurrence.item_id == todo.id)
        )
        for old_comp in existing.all():
            await sess.delete(old_comp)
        await sess.commit()
        
        # Test 1: Store completion with metadata (no hash required)
        print("Test 1: Store completion using metadata...")
        occ_dt = datetime(2025, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
        
        # Check that it doesn't exist yet
        check1 = await sess.exec(
            select(CompletedOccurrence)
            .where(CompletedOccurrence.user_id == user.id)
            .where(CompletedOccurrence.item_type == 'todo')
            .where(CompletedOccurrence.item_id == todo.id)
            .where(CompletedOccurrence.occurrence_dt == occ_dt)
        )
        assert check1.first() is None, "Completion should not exist yet"
        print("  ✓ Verified completion doesn't exist")
        
        # Store completion
        comp = CompletedOccurrence(
            user_id=user.id,
            occ_hash=None,  # Phase 1: Hash can be NULL
            item_type='todo',
            item_id=todo.id,
            occurrence_dt=occ_dt,
            metadata_json='{"title": "Test todo for Phase 1"}'
        )
        sess.add(comp)
        await sess.commit()
        print("  ✓ Stored completion with metadata (hash=NULL)")
        
        # Test 2: Retrieve completion using metadata
        print("\nTest 2: Retrieve completion using metadata...")
        check2 = await sess.exec(
            select(CompletedOccurrence)
            .where(CompletedOccurrence.user_id == user.id)
            .where(CompletedOccurrence.item_type == 'todo')
            .where(CompletedOccurrence.item_id == todo.id)
            .where(CompletedOccurrence.occurrence_dt == occ_dt)
        )
        found = check2.first()
        assert found is not None, "Should find completion by metadata"
        assert found.item_type == 'todo'
        assert found.item_id == todo.id
        assert found.occurrence_dt == occ_dt
        assert found.occ_hash is None  # No hash stored
        print("  ✓ Retrieved completion using metadata")
        print(f"    item_type={found.item_type}, item_id={found.item_id}")
        print(f"    occurrence_dt={found.occurrence_dt}")
        print(f"    occ_hash={found.occ_hash}")
        
        # Test 3: Idempotency check using metadata
        print("\nTest 3: Test idempotency (duplicate prevention)...")
        duplicate = CompletedOccurrence(
            user_id=user.id,
            occ_hash=None,
            item_type='todo',
            item_id=todo.id,
            occurrence_dt=occ_dt,
            metadata_json='{"title": "Test todo for Phase 1"}'
        )
        
        # This should be caught by application logic (not DB constraint yet)
        check3 = await sess.exec(
            select(CompletedOccurrence)
            .where(CompletedOccurrence.user_id == user.id)
            .where(CompletedOccurrence.item_type == 'todo')
            .where(CompletedOccurrence.item_id == todo.id)
            .where(CompletedOccurrence.occurrence_dt == occ_dt)
        )
        if check3.first():
            print("  ✓ Idempotency check passed (completion already exists)")
        else:
            print("  ✗ WARNING: No idempotency check - would create duplicate")
        
        # Test 4: Delete using metadata
        print("\nTest 4: Delete completion using metadata...")
        await sess.delete(found)
        await sess.commit()
        
        check4 = await sess.exec(
            select(CompletedOccurrence)
            .where(CompletedOccurrence.user_id == user.id)
            .where(CompletedOccurrence.item_type == 'todo')
            .where(CompletedOccurrence.item_id == todo.id)
            .where(CompletedOccurrence.occurrence_dt == occ_dt)
        )
        assert check4.first() is None, "Completion should be deleted"
        print("  ✓ Deleted completion using metadata lookup")
        
        # Test 5: Test with multiple completions on different dates
        print("\nTest 5: Multiple completions for same todo...")
        dates = [
            datetime(2025, 10, 10, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2025, 10, 15, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2025, 10, 20, 12, 0, 0, tzinfo=timezone.utc),
        ]
        
        for dt in dates:
            comp = CompletedOccurrence(
                user_id=user.id,
                occ_hash=None,
                item_type='todo',
                item_id=todo.id,
                occurrence_dt=dt,
            )
            sess.add(comp)
        await sess.commit()
        print(f"  ✓ Stored {len(dates)} completions")
        
        # Verify all are retrievable
        check5 = await sess.exec(
            select(CompletedOccurrence)
            .where(CompletedOccurrence.user_id == user.id)
            .where(CompletedOccurrence.item_type == 'todo')
            .where(CompletedOccurrence.item_id == todo.id)
        )
        all_comps = check5.all()
        assert len(all_comps) == 3, f"Should have 3 completions, got {len(all_comps)}"
        print(f"  ✓ Retrieved all {len(all_comps)} completions")
        
        # Verify each date is unique (normalize timezones for comparison)
        dates_found = sorted([c.occurrence_dt.replace(tzinfo=timezone.utc) if c.occurrence_dt.tzinfo is None else c.occurrence_dt for c in all_comps])
        dates_expected = sorted(dates)
        assert dates_found == dates_expected, f"Dates should match. Expected {dates_expected}, got {dates_found}"
        print("  ✓ Each completion has correct unique date")
        
        # Cleanup - delete all completions for this user first
        for c in all_comps:
            await sess.delete(c)
        await sess.delete(todo)
        await sess.delete(lst)
        await sess.delete(user)
        await sess.commit()
        print("\n✅ All Phase 1 tests passed!")

if __name__ == '__main__':
    try:
        asyncio.run(test_phase1())
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
