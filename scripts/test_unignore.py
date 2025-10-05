#!/usr/bin/env python3
"""
Test unignoring todos - both calendar_ignored flag and todo_from scope.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__) + '/..')

import asyncio
from app.db import async_session
from app.models import Todo
from sqlmodel import select

async def test_unignore():
    """Check todo 549 and 404 ignore status and test unignoring."""
    
    async with async_session() as sess:
        # Check todo 549
        todo_549 = await sess.get(Todo, 549)
        if todo_549:
            print(f"📌 Todo 549:")
            print(f"  Text: {todo_549.text[:80]}")
            print(f"  calendar_ignored: {todo_549.calendar_ignored}")
            print(f"  List ID: {todo_549.list_id}")
        else:
            print("❌ Todo 549 not found")
        
        print()
        
        # Check todo 404
        todo_404 = await sess.get(Todo, 404)
        if todo_404:
            print(f"📌 Todo 404:")
            print(f"  Text: {todo_404.text[:80]}")
            print(f"  calendar_ignored: {todo_404.calendar_ignored}")
            print(f"  List ID: {todo_404.list_id}")
        else:
            print("❌ Todo 404 not found")
        
        print()
        
        # Check for ignored scopes for todo 404
        from app.models import IgnoredScope
        ignored_scopes_result = await sess.exec(
            select(IgnoredScope)
        )
        all_scopes = ignored_scopes_result.all()
        
        print(f"📋 Found {len(all_scopes)} IgnoredScope records in database")
        
        # Check for scopes related to todo 404
        todo_404_scopes = [s for s in all_scopes if '404' in s.scope_hash or (s.scope_key and '404' in str(s.scope_key))]
        if todo_404_scopes:
            print(f"\n  Scopes related to todo 404:")
            for scope in todo_404_scopes:
                print(f"    - ID {scope.id}: type={scope.scope_type}, key={scope.scope_key}, hash={scope.scope_hash}")
        
        # Check for scopes related to todo 549
        todo_549_scopes = [s for s in all_scopes if '549' in s.scope_hash or (s.scope_key and '549' in str(s.scope_key))]
        if todo_549_scopes:
            print(f"\n  Scopes related to todo 549:")
            for scope in todo_549_scopes:
                print(f"    - ID {scope.id}: type={scope.scope_type}, key={scope.scope_key}, hash={scope.scope_hash}")
        
        print("\n" + "="*80)
        print("Testing unignore operations:")
        print("="*80)
        
        # Test 1: Unset calendar_ignored for todo 549
        if todo_549 and todo_549.calendar_ignored:
            print("\nTest 1: Unsetting calendar_ignored for todo 549")
            todo_549.calendar_ignored = False
            sess.add(todo_549)
            await sess.commit()
            await sess.refresh(todo_549)
            print(f"  ✓ calendar_ignored now: {todo_549.calendar_ignored}")
        
        # Test 2: Remove todo_from scope for todo 404
        if todo_404_scopes:
            print("\nTest 2: Removing IgnoredScope for todo 404")
            for scope in todo_404_scopes:
                print(f"  Deleting scope ID {scope.id}")
                await sess.delete(scope)
            await sess.commit()
            print(f"  ✓ Deleted {len(todo_404_scopes)} scope(s)")
        
        print("\n✅ Tests complete")

if __name__ == '__main__':
    asyncio.run(test_unignore())
