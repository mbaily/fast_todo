#!/usr/bin/env python3
"""
Test Phase 2: Verify server no longer generates or requires occ_hash.
Tests that completions can be stored and retrieved using only metadata,
and that occurrences are generated with occ_id instead of occ_hash.
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
async def test_phase2():
    """Test that server generates occ_id and accepts completions without hash."""
    import uuid
    
    async with async_session() as sess:
        # Create test user
        username = f'phase2_test_{uuid.uuid4().hex[:8]}'
        user = User(username=username, password_hash='test')
        sess.add(user)
        await sess.commit()
        await sess.refresh(user)
        
        # Create test list and todo
        lst = ListState(name='Phase 2 Test List', owner_id=user.id)
        sess.add(lst)
        await sess.commit()
        await sess.refresh(lst)
        
        todo = Todo(text='Phase 2 test todo', list_id=lst.id)
        sess.add(todo)
        await sess.commit()
        await sess.refresh(todo)
        
        print(f"Created test user: {user.username} (id={user.id})")
        print(f"Created test todo: {todo.id}\n")
        
        # Test 1: Create completion with NO hash (Phase 2)
        print("Test 1: Store completion without hash (Phase 2 behavior)...")
        occ_dt = datetime(2025, 10, 20, 14, 30, 0, tzinfo=timezone.utc)
        
        comp = CompletedOccurrence(
            user_id=user.id,
            occ_hash=None,  # Phase 2: No hash!
            item_type='todo',
            item_id=todo.id,
            occurrence_dt=occ_dt,
            metadata_json='{"title": "Phase 2 test todo"}'
        )
        sess.add(comp)
        await sess.commit()
        await sess.refresh(comp)
        
        assert comp.occ_hash is None, "Hash should be NULL"
        assert comp.item_type == 'todo'
        assert comp.item_id == todo.id
        # Compare dates without timezone (SQLite may strip timezone)
        assert comp.occurrence_dt.replace(tzinfo=None) == occ_dt.replace(tzinfo=None)
        print("  ✓ Stored completion with NULL hash")
        print(f"    Completion ID: {comp.id}")
        print(f"    Hash: {comp.occ_hash}")
        print(f"    Metadata: {comp.item_type}:{comp.item_id}:{comp.occurrence_dt}")
        
        # Test 2: Retrieve using only metadata
        print("\nTest 2: Retrieve completion using metadata (no hash lookup)...")
        check = await sess.exec(
            select(CompletedOccurrence)
            .where(CompletedOccurrence.user_id == user.id)
            .where(CompletedOccurrence.item_type == 'todo')
            .where(CompletedOccurrence.item_id == todo.id)
            .where(CompletedOccurrence.occurrence_dt == occ_dt)
        )
        found = check.first()
        assert found is not None, "Should find completion by metadata"
        assert found.id == comp.id
        assert found.occ_hash is None
        print("  ✓ Retrieved completion using only metadata")
        
        # Test 3: Verify calendar endpoint works
        print("\nTest 3: Test calendar endpoint generates occ_id instead of occ_hash...")
        from app.main import calendar_occurrences
        from unittest.mock import Mock
        
        # Mock request and user
        mock_request = Mock()
        mock_request.query_params = Mock()
        mock_request.query_params.get = Mock(return_value=None)
        
        mock_user = Mock()
        mock_user.id = user.id
        mock_user.username = username
        
        # Call calendar occurrences for Oct 2025
        start = datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
        
        result = await calendar_occurrences(
            mock_request,
            start=start.isoformat(),
            end=end.isoformat(),
            current_user=mock_user
        )
        
        assert 'occurrences' in result
        print(f"  ✓ Calendar endpoint returned {len(result['occurrences'])} occurrences")
        
        # Check that occurrences have occ_id but NOT occ_hash
        if result['occurrences']:
            sample = result['occurrences'][0]
            print(f"\n  Sample occurrence:")
            print(f"    Has 'occ_id': {'occ_id' in sample}")
            print(f"    Has 'occ_hash': {'occ_hash' in sample}")
            if 'occ_id' in sample:
                print(f"    occ_id value: {sample['occ_id']}")
            if 'occ_hash' in sample:
                print(f"    occ_hash value: {sample.get('occ_hash')}")
            
            # Phase 2: occ_hash should be None, occ_id should exist
            if 'occ_hash' in sample:
                assert sample['occ_hash'] is None, "occ_hash should be None in Phase 2"
            assert 'occ_id' in sample or sample.get('occ_hash') is not None, "Should have occ_id or legacy hash"
            print("  ✓ Occurrence structure correct for Phase 2")
        
        # Test 4: Multiple completions without hash
        print("\nTest 4: Multiple Phase 2 completions (all with NULL hash)...")
        dates = [
            datetime(2025, 10, 21, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2025, 10, 22, 11, 0, 0, tzinfo=timezone.utc),
            datetime(2025, 10, 23, 12, 0, 0, tzinfo=timezone.utc),
        ]
        
        for dt in dates:
            c = CompletedOccurrence(
                user_id=user.id,
                occ_hash=None,  # Phase 2: All NULL
                item_type='todo',
                item_id=todo.id,
                occurrence_dt=dt,
            )
            sess.add(c)
        await sess.commit()
        print(f"  ✓ Stored {len(dates)} more completions (all with NULL hash)")
        
        # Verify all are retrievable
        check_all = await sess.exec(
            select(CompletedOccurrence)
            .where(CompletedOccurrence.user_id == user.id)
            .where(CompletedOccurrence.item_type == 'todo')
            .where(CompletedOccurrence.item_id == todo.id)
        )
        all_comps = check_all.all()
        assert len(all_comps) == 4, f"Should have 4 completions, got {len(all_comps)}"
        
        # Verify all have NULL hash
        null_hash_count = sum(1 for c in all_comps if c.occ_hash is None)
        assert null_hash_count == 4, f"All should have NULL hash, but {null_hash_count}/4 do"
        print(f"  ✓ All {len(all_comps)} completions have NULL hash (Phase 2)")
        
        # Cleanup
        await sess.delete(todo)
        await sess.delete(lst)
        await sess.delete(user)
        await sess.commit()
        print("\n✅ All Phase 2 tests passed!")

if __name__ == '__main__':
    try:
        asyncio.run(test_phase2())
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
