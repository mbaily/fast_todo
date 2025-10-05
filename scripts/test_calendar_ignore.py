#!/usr/bin/env python3
"""
Test calendar ignore functionality using calendar_ignored flag.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__) + '/..')

import asyncio
from datetime import datetime, timezone
from app.db import async_session
from app.models import User, Todo, ListState
from sqlmodel import select

async def test_calendar_ignore():
    """Test ignoring a todo from calendar using calendar_ignored flag."""
    
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
            print(f"✓ Created test list: {lst.id}\n")
        
        # Create a test todo with a date
        test_todo = Todo(
            text="Test ignore todo 2025-10-15",
            list_id=lst.id,
            calendar_ignored=False
        )
        sess.add(test_todo)
        await sess.commit()
        await sess.refresh(test_todo)
        
        print(f"✓ Created test todo: {test_todo.id}")
        print(f"  Text: {test_todo.text}")
        print(f"  calendar_ignored: {test_todo.calendar_ignored}\n")
        
        # Test calendar occurrences before ignoring
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
        
        print("Test 1: Todo should appear in calendar (not ignored)")
        result = await calendar_occurrences(
            mock_request,
            start=start.isoformat(),
            end=end.isoformat(),
            current_user=mock_user
        )
        
        occs = result.get('occurrences', [])
        test_occ = [o for o in occs if o.get('id') == test_todo.id]
        
        if test_occ:
            print(f"  ✓ Found {len(test_occ)} occurrence(s) for test todo")
        else:
            print(f"  ⚠️  Test todo not found in calendar (might not have valid date)")
        
        # Set calendar_ignored flag
        print("\nTest 2: Setting calendar_ignored=True")
        test_todo.calendar_ignored = True
        sess.add(test_todo)
        await sess.commit()
        await sess.refresh(test_todo)
        print(f"  ✓ calendar_ignored set to: {test_todo.calendar_ignored}")
        
        # Test calendar occurrences after ignoring (without include_ignored)
        print("\nTest 3: Todo should NOT appear in calendar (ignored)")
        result = await calendar_occurrences(
            mock_request,
            start=start.isoformat(),
            end=end.isoformat(),
            current_user=mock_user
        )
        
        occs = result.get('occurrences', [])
        test_occ = [o for o in occs if o.get('id') == test_todo.id]
        
        if not test_occ:
            print(f"  ✓ Test todo correctly filtered out (ignored)")
        else:
            print(f"  ❌ Test todo still appears ({len(test_occ)} occurrences)")
        
        # Test calendar occurrences with include_ignored
        print("\nTest 4: Todo should appear with ignored flag (include_ignored=True)")
        mock_request.query_params.get = Mock(side_effect=lambda k, default=None: 
            '1' if k == 'include_ignored' else default)
        
        result = await calendar_occurrences(
            mock_request,
            start=start.isoformat(),
            end=end.isoformat(),
            current_user=mock_user
        )
        
        occs = result.get('occurrences', [])
        test_occ = [o for o in occs if o.get('id') == test_todo.id]
        
        if test_occ:
            print(f"  ✓ Found {len(test_occ)} occurrence(s) for test todo")
            occ = test_occ[0]
            print(f"  Ignored: {occ.get('ignored')}")
            print(f"  Ignored scopes: {occ.get('ignored_scopes')}")
            
            if 'calendar_ignored' in occ.get('ignored_scopes', []):
                print(f"  ✓ Correctly marked with 'calendar_ignored' scope")
            else:
                print(f"  ⚠️  'calendar_ignored' not in ignored_scopes")
        else:
            print(f"  ❌ Test todo not found even with include_ignored")
        
        # Unset calendar_ignored
        print("\nTest 5: Unsetting calendar_ignored=False")
        test_todo.calendar_ignored = False
        sess.add(test_todo)
        await sess.commit()
        await sess.refresh(test_todo)
        print(f"  ✓ calendar_ignored set to: {test_todo.calendar_ignored}")
        
        # Cleanup
        await sess.delete(test_todo)
        await sess.commit()
        print("\n✓ Test complete (cleaned up test todo)")

if __name__ == '__main__':
    asyncio.run(test_calendar_ignore())
