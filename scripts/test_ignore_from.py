#!/usr/bin/env python3
"""
Test the 'ignore from this date' functionality.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__) + '/..')

import asyncio
from datetime import datetime, timezone
from app.db import async_session
from app.models import User, Todo, ListState, IgnoredScope
from sqlmodel import select

async def test_ignore_from():
    """Test creating a todo_from ignore and checking calendar filtering."""
    
    async with async_session() as sess:
        # Get first user
        result = await sess.exec(select(User).limit(1))
        user = result.first()
        
        if not user:
            print("❌ No users found!")
            return
            
        print(f"✓ Testing with user: {user.username} (id={user.id})\n")
        
        # Get or create a test list
        list_result = await sess.exec(select(ListState).where(ListState.owner_id == user.id).limit(1))
        lst = list_result.first()
        
        if not lst:
            lst = ListState(name="Test List", owner_id=user.id)
            sess.add(lst)
            await sess.commit()
            await sess.refresh(lst)
        
        # Create a recurring test todo
        test_todo = Todo(
            text="Test ignore from todo every day",
            recurrence_rrule="FREQ=DAILY",
            list_id=lst.id,
            calendar_ignored=False
        )
        sess.add(test_todo)
        await sess.commit()
        await sess.refresh(test_todo)
        
        print(f"✓ Created test todo: {test_todo.id}")
        print(f"  Text: {test_todo.text}")
        print(f"  RRule: {test_todo.recurrence_rrule}\n")
        
        # Test calendar occurrences without ignore
        from app.main import calendar_occurrences
        from unittest.mock import Mock
        
        mock_request = Mock()
        mock_request.query_params = Mock()
        mock_request.query_params.get = Mock(return_value=None)
        
        mock_user = Mock()
        mock_user.id = user.id
        mock_user.username = user.username
        
        start = datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
        
        print("Test 1: Get occurrences without ignore")
        result = await calendar_occurrences(
            mock_request,
            start=start.isoformat(),
            end=end.isoformat(),
            current_user=mock_user
        )
        
        occs = [o for o in result.get('occurrences', []) if o.get('id') == test_todo.id]
        print(f"  Found {len(occs)} occurrences for test todo")
        if occs:
            print(f"  First occurrence: {occs[0].get('occurrence_dt')}")
            print(f"  Last occurrence: {occs[-1].get('occurrence_dt')}")
        
        # Create an ignore_from scope for Oct 15
        from_dt = datetime(2025, 10, 15, 0, 0, 0, tzinfo=timezone.utc)
        print(f"\nTest 2: Create ignore_from scope for {from_dt.isoformat()}")
        
        from app.utils import ignore_todo_from_hash
        scope_hash = ignore_todo_from_hash(str(test_todo.id), from_dt)
        
        ignore_scope = IgnoredScope(
            user_id=user.id,
            scope_type='todo_from',
            scope_key=str(test_todo.id),
            scope_hash=scope_hash,
            from_dt=from_dt
        )
        sess.add(ignore_scope)
        await sess.commit()
        await sess.refresh(ignore_scope)
        
        print(f"  ✓ Created IgnoredScope: ID={ignore_scope.id}")
        print(f"    scope_type: {ignore_scope.scope_type}")
        print(f"    scope_key: {ignore_scope.scope_key}")
        print(f"    from_dt: {ignore_scope.from_dt}")
        
        # Test calendar occurrences with ignore_from
        print("\nTest 3: Get occurrences with ignore_from (should filter out >= Oct 15)")
        result = await calendar_occurrences(
            mock_request,
            start=start.isoformat(),
            end=end.isoformat(),
            current_user=mock_user
        )
        
        occs = [o for o in result.get('occurrences', []) if o.get('id') == test_todo.id]
        print(f"  Found {len(occs)} occurrences for test todo (after ignore)")
        if occs:
            print(f"  First occurrence: {occs[0].get('occurrence_dt')}")
            print(f"  Last occurrence: {occs[-1].get('occurrence_dt')}")
            
            # Check if any are >= Oct 15
            from dateutil.parser import parse as parse_date
            filtered_out = []
            for occ in occs:
                occ_dt = parse_date(occ.get('occurrence_dt'))
                if occ_dt >= from_dt:
                    filtered_out.append(occ_dt.isoformat())
            
            if filtered_out:
                print(f"\n  ❌ BUG: Found {len(filtered_out)} occurrences that should be filtered:")
                for dt in filtered_out[:5]:
                    print(f"    - {dt}")
            else:
                print(f"  ✓ Correctly filtered out all occurrences >= Oct 15")
        else:
            print(f"  ⚠️  No occurrences found (all filtered or none generated)")
        
        # Test with include_ignored
        print("\nTest 4: Get occurrences with include_ignored=True")
        mock_request.query_params.get = Mock(side_effect=lambda k, default=None: 
            '1' if k == 'include_ignored' else default)
        
        result = await calendar_occurrences(
            mock_request,
            start=start.isoformat(),
            end=end.isoformat(),
            current_user=mock_user,
            include_ignored=True
        )
        
        occs = [o for o in result.get('occurrences', []) if o.get('id') == test_todo.id]
        print(f"  Found {len(occs)} occurrences for test todo")
        
        ignored_occs = [o for o in occs if o.get('ignored')]
        print(f"  Marked as ignored: {len(ignored_occs)}")
        
        if ignored_occs:
            print(f"  Sample ignored occurrence:")
            sample = ignored_occs[0]
            print(f"    Date: {sample.get('occurrence_dt')}")
            print(f"    Ignored: {sample.get('ignored')}")
            print(f"    Scopes: {sample.get('ignored_scopes')}")
        
        # Cleanup
        await sess.delete(ignore_scope)
        await sess.delete(test_todo)
        await sess.commit()
        print("\n✓ Test complete (cleaned up)")

if __name__ == '__main__':
    asyncio.run(test_ignore_from())
