#!/usr/bin/env python3
"""
Debug script to check calendar occurrences generation.
Tests if the server is correctly generating occurrences with occ_id.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__) + '/..')

import asyncio
from datetime import datetime, timezone, timedelta
from app.db import async_session
from app.models import User, Todo, ListState
from sqlmodel import select

async def debug_calendar():
    """Check what calendar occurrences are being generated."""
    
    async with async_session() as sess:
        # Get first user
        result = await sess.exec(select(User).limit(1))
        user = result.first()
        
        if not user:
            print("❌ No users found in database!")
            return
            
        print(f"✓ Found user: {user.username} (id={user.id})\n")
        
        # Get lists and todos
        lists_result = await sess.exec(select(ListState).where(ListState.owner_id == user.id))
        lists = lists_result.all()
        print(f"✓ Found {len(lists)} lists")
        
        todos_result = await sess.exec(select(Todo))
        todos = todos_result.all()
        print(f"✓ Found {len(todos)} todos\n")
        
        if not todos:
            print("⚠️  No todos found - creating a test todo...")
            if lists:
                test_todo = Todo(
                    text="Debug test todo 2025-10-15",
                    list_id=lists[0].id
                )
                sess.add(test_todo)
                await sess.commit()
                await sess.refresh(test_todo)
                print(f"✓ Created test todo: {test_todo.id}")
                todos = [test_todo]
        
        # Test calendar occurrences endpoint
        from app.main import calendar_occurrences
        from unittest.mock import Mock
        
        mock_request = Mock()
        mock_request.query_params = Mock()
        mock_request.query_params.get = Mock(return_value=None)
        
        mock_user = Mock()
        mock_user.id = user.id
        mock_user.username = user.username
        
        # Get occurrences for current month
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, 1, 0, 0, 0, tzinfo=timezone.utc)
        if now.month == 12:
            end = datetime(now.year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)
        else:
            end = datetime(now.year, now.month + 1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)
        
        print(f"\n📅 Requesting calendar occurrences:")
        print(f"   Start: {start.isoformat()}")
        print(f"   End: {end.isoformat()}\n")
        
        try:
            result = await calendar_occurrences(
                mock_request,
                start=start.isoformat(),
                end=end.isoformat(),
                current_user=mock_user
            )
            
            print(f"✓ Calendar endpoint returned successfully")
            print(f"  Keys in response: {list(result.keys())}")
            
            if 'occurrences' in result:
                occs = result['occurrences']
                print(f"\n📊 Occurrences returned: {len(occs)}")
                
                if len(occs) == 0:
                    print("\n⚠️  NO OCCURRENCES RETURNED!")
                    print("\nPossible causes:")
                    print("  1. Todos have no dates in their text")
                    print("  2. Dates are outside the requested range")
                    print("  3. All occurrences are filtered out (completed/ignored)")
                    print("  4. Error in occurrence generation logic")
                    
                    # Show some todos to help debug
                    print(f"\n📝 Sample todos:")
                    for i, todo in enumerate(todos[:5]):
                        print(f"  {i+1}. {todo.text[:80]}")
                else:
                    print("\n📋 Sample occurrences:")
                    for i, occ in enumerate(occs[:5]):
                        print(f"\n  Occurrence {i+1}:")
                        print(f"    Title: {occ.get('title', 'N/A')}")
                        print(f"    Date: {occ.get('occurrence_dt', 'N/A')}")
                        print(f"    Type: {occ.get('item_type', 'N/A')}")
                        print(f"    ID: {occ.get('id', 'N/A')}")
                        print(f"    occ_id: {occ.get('occ_id', 'N/A')}")
                        print(f"    occ_hash: {occ.get('occ_hash', 'N/A')}")
                        print(f"    completed: {occ.get('completed', False)}")
                        
                        # Check for Phase 2 compliance
                        if 'occ_id' not in occ:
                            print(f"    ⚠️  WARNING: Missing occ_id field!")
                        if occ.get('occ_hash') is not None:
                            print(f"    ⚠️  WARNING: occ_hash should be None (Phase 2)")
            else:
                print("\n❌ 'occurrences' key not in response!")
                print(f"   Response: {result}")
                
        except Exception as e:
            print(f"\n❌ Error calling calendar_occurrences: {e}")
            import traceback
            traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(debug_calendar())
