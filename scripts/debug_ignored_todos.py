#!/usr/bin/env python3
"""
Debug script to check which todos are calendar_ignored and what shows with include_ignored.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__) + '/..')

import asyncio
from datetime import datetime, timezone
from app.db import async_session
from app.models import User, Todo
from sqlmodel import select

async def debug_ignored_todos():
    """Check which todos are marked as calendar_ignored and test calendar endpoint."""
    
    async with async_session() as sess:
        # Get first user
        result = await sess.exec(select(User).limit(1))
        user = result.first()
        
        if not user:
            print("❌ No users found!")
            return
            
        print(f"✓ User: {user.username} (id={user.id})\n")
        
        # Find all todos with calendar_ignored=True
        ignored_result = await sess.exec(
            select(Todo).where(Todo.calendar_ignored == True)
        )
        ignored_todos = ignored_result.all()
        
        print(f"📋 Found {len(ignored_todos)} todos with calendar_ignored=True:")
        for todo in ignored_todos:
            print(f"  - ID {todo.id}: {todo.text[:80]}")
        print()
        
        # Check todo 404 specifically
        todo_404 = await sess.get(Todo, 404)
        if todo_404:
            print(f"📌 Todo 404 details:")
            print(f"  Text: {todo_404.text[:80]}")
            print(f"  calendar_ignored: {todo_404.calendar_ignored}")
            print(f"  List ID: {todo_404.list_id}")
        else:
            print(f"⚠️  Todo 404 not found")
        print()
        
        # Test calendar with include_ignored=False
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
        
        print("Test 1: Calendar without include_ignored (default)")
        result = await calendar_occurrences(
            mock_request,
            start=start.isoformat(),
            end=end.isoformat(),
            current_user=mock_user
        )
        
        occs = result.get('occurrences', [])
        print(f"  Total occurrences: {len(occs)}")
        
        # Check if any ignored todos appear
        for todo in ignored_todos:
            found = [o for o in occs if o.get('id') == todo.id]
            if found:
                print(f"  ⚠️  calendar_ignored todo {todo.id} still appears!")
        
        # Check todo 404
        todo_404_occs = [o for o in occs if o.get('id') == 404]
        print(f"  Todo 404 occurrences: {len(todo_404_occs)}")
        
        print("\nTest 2: Calendar WITH include_ignored=True")
        mock_request.query_params.get = Mock(side_effect=lambda k, default=None: 
            '1' if k == 'include_ignored' else default)
        
        result = await calendar_occurrences(
            mock_request,
            start=start.isoformat(),
            end=end.isoformat(),
            current_user=mock_user,
            include_ignored=True
        )
        
        occs = result.get('occurrences', [])
        print(f"  Total occurrences: {len(occs)}")
        
        # Check for ignored todos
        ignored_count = 0
        calendar_ignored_count = 0
        
        for todo in ignored_todos:
            found = [o for o in occs if o.get('id') == todo.id]
            if found:
                print(f"\n  ✓ Found calendar_ignored todo {todo.id}:")
                occ = found[0]
                print(f"    Title: {occ.get('title', '')[:60]}")
                print(f"    Ignored: {occ.get('ignored')}")
                print(f"    Ignored scopes: {occ.get('ignored_scopes', [])}")
                
                if occ.get('ignored'):
                    ignored_count += 1
                if 'calendar_ignored' in occ.get('ignored_scopes', []):
                    calendar_ignored_count += 1
        
        print(f"\n  Summary:")
        print(f"    Calendar_ignored todos found: {len([o for o in occs if o.get('id') in [t.id for t in ignored_todos]])}")
        print(f"    Marked as ignored: {ignored_count}")
        print(f"    With 'calendar_ignored' scope: {calendar_ignored_count}")
        
        # Check todo 404 specifically
        todo_404_occs = [o for o in occs if o.get('id') == 404]
        if todo_404_occs:
            print(f"\n  📌 Todo 404 in results:")
            occ = todo_404_occs[0]
            print(f"    Title: {occ.get('title', '')[:60]}")
            print(f"    Ignored: {occ.get('ignored')}")
            print(f"    Ignored scopes: {occ.get('ignored_scopes', [])}")
        
        # Check for any occurrences with 'ignored' flag
        all_ignored = [o for o in occs if o.get('ignored')]
        print(f"\n  All ignored occurrences: {len(all_ignored)}")
        if all_ignored:
            print(f"  Sample ignored occurrences:")
            for i, occ in enumerate(all_ignored[:5]):
                print(f"    {i+1}. ID {occ.get('id')}: {occ.get('title', '')[:50]} - scopes: {occ.get('ignored_scopes', [])}")

if __name__ == '__main__':
    asyncio.run(debug_ignored_todos())
