#!/usr/bin/env python3
"""
Debug script to check what occurrences are generated for todos 549, 392, 397
and whether phantoms are being created inappropriately.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio
from datetime import datetime, timezone
from app.db import async_session
from app.models import User, CompletedOccurrence, Todo
from sqlmodel import select
import json

async def main():
    # Simulate fetching calendar occurrences for October 2025
    async with async_session() as sess:
        # Get the mbaily user
        user_result = await sess.exec(select(User).where(User.username == 'mbaily'))
        user = user_result.first()
        if not user:
            print("User not found")
            return
        
        print(f"User: {user.username} (id={user.id})\n")
        
        # Check todos
        todo_ids = [549, 392, 397]
        for todo_id in todo_ids:
            todo = await sess.get(Todo, todo_id)
            if not todo:
                print(f"Todo {todo_id} not found")
                continue
            
            print(f"=== TODO {todo_id} ===")
            print(f"Text: {todo.text}")
            print(f"RRULE: {todo.recurrence_rrule}")
            print(f"DTStart: {todo.recurrence_dtstart}")
            
            # Get completed occurrences
            comp_result = await sess.exec(
                select(CompletedOccurrence).where(
                    CompletedOccurrence.item_type == 'todo',
                    CompletedOccurrence.item_id == todo_id,
                    CompletedOccurrence.user_id == user.id
                )
            )
            comps = comp_result.all()
            print(f"Completed occurrences: {len(comps)}")
            for comp in comps:
                print(f"  - occurrence_dt: {comp.occurrence_dt}")
                print(f"    occ_hash: {comp.occ_hash}")
                print(f"    completed_at: {comp.completed_at}")
            
            # Now simulate what the calendar would generate for October 2025
            if todo.recurrence_rrule:
                from dateutil.rrule import rrulestr
                from app.utils import occurrence_hash
                
                # Generate occurrences for October 2025
                start = datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
                end = datetime(2025, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
                
                dtstart = todo.recurrence_dtstart
                if dtstart and dtstart.tzinfo is None:
                    dtstart = dtstart.replace(tzinfo=timezone.utc)
                
                try:
                    rrule = rrulestr(todo.recurrence_rrule, dtstart=dtstart)
                    occurrences = list(rrule.between(start, end, inc=True))
                    
                    print(f"Generated occurrences for Oct 2025: {len(occurrences)}")
                    for occ in occurrences[:5]:  # Show first 5
                        # Normalize to midnight
                        occ_normalized = occ.replace(hour=0, minute=0, second=0, microsecond=0)
                        occ_hash_val = occurrence_hash('todo', todo_id, occ_normalized, 
                                                      todo.recurrence_rrule, todo.text)
                        print(f"  - {occ_normalized.isoformat()} -> hash: {occ_hash_val}")
                        
                        # Check if this matches any completed occurrence
                        for comp in comps:
                            comp_dt = comp.occurrence_dt
                            if comp_dt.tzinfo is None:
                                comp_dt = comp_dt.replace(tzinfo=timezone.utc)
                            comp_dt_normalized = comp_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                            
                            if occ_normalized.date() == comp_dt_normalized.date():
                                print(f"    MATCH with completed: {comp.occ_hash}")
                                if occ_hash_val == comp.occ_hash:
                                    print(f"    ✓ Hash matches!")
                                else:
                                    print(f"    ✗ Hash MISMATCH!")
                                    print(f"      Generated: {occ_hash_val}")
                                    print(f"      Stored:    {comp.occ_hash}")
                                
                except Exception as e:
                    print(f"Error generating occurrences: {e}")
            else:
                print("Not a recurring todo")
            
            print()

if __name__ == '__main__':
    asyncio.run(main())
