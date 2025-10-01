#!/usr/bin/env python3
"""
Test what happens when user changes the recurrence rule completely.

Scenario:
1. Todo: "Standup every Monday" 
   -> Generates occurrences: Oct 6, Oct 13, Oct 20, Oct 27
2. User completes Oct 13
3. User changes to: "Standup every Tuesday"
   -> Now generates: Oct 7, Oct 14, Oct 21, Oct 28
4. Question: Does Oct 13 (the completed Monday) still show as completed?
"""
import asyncio
import os
import pytest
pytestmark = pytest.mark.asyncio
from datetime import datetime, timezone, timedelta


async def _async_test_rrule_change():
    os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///./fast_todo.db')
    
    from sqlmodel import select
    from app.db import async_session, init_db
    from app.models import User, ListState, Todo, CompletedOccurrence
    from app.utils import occurrence_hash, parse_text_to_rrule_string
    from app.main import calendar_occurrences
    from unittest.mock import Mock
    from fastapi import Request
    
    await init_db()
    
    print("=" * 70)
    print("Testing: User changes recurrence rule completely")
    print("=" * 70)
    print()
    
    async with async_session() as sess:
        # Get user and list
        result = await sess.exec(select(User).limit(1))
        user = result.first()
        result = await sess.exec(select(ListState).where(ListState.owner_id == user.id).limit(1))
        test_list = result.first()
        
        # Create recurring todo - every Monday
        original_text = 'Team standup every Monday'
        dtstart, rrule = parse_text_to_rrule_string(original_text)
        
        todo = Todo(
            text=original_text,
            list_id=test_list.id,
            recurrence_rrule=rrule,
            recurrence_dtstart=dtstart
        )
        sess.add(todo)
        await sess.commit()
        await sess.refresh(todo)
        
        print(f"1. Created recurring todo: '{todo.text}'")
        print(f"   RRULE: {rrule}")
        print(f"   DTSTART: {dtstart}")
        print()
        
        # Query calendar to see occurrences
        mock_request = Mock(spec=Request)
        mock_request.headers = {}
        
        class MockUser:
            id = user.id
        
        start = datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
        
        result = await calendar_occurrences(
            mock_request,
            start=start.isoformat(),
            end=end.isoformat(),
            current_user=MockUser()
        )
        
        our_occs = [o for o in result.get('occurrences', []) if o.get('id') == todo.id]
        print(f"2. Initial occurrences in October:")
        for o in our_occs[:5]:  # Show first 5
            dt = datetime.fromisoformat(o['occurrence_dt'].replace('Z', '+00:00'))
            print(f"   - {dt.strftime('%a %b %d')} ({o['occurrence_dt']})")
        print()
        
        # Pick one to complete (e.g., Oct 13 - second Monday)
        if len(our_occs) >= 2:
            to_complete = our_occs[1]  # Second occurrence
            comp_dt_str = to_complete['occurrence_dt']
            comp_dt = datetime.fromisoformat(comp_dt_str.replace('Z', '+00:00'))
            comp_hash = to_complete['occ_hash']
            
            print(f"3. Completing: {comp_dt.strftime('%a %b %d')} ({comp_dt_str})")
            
            comp = CompletedOccurrence(
                user_id=user.id,
                occ_hash=comp_hash,
                item_type='todo',
                item_id=todo.id,
                occurrence_dt=comp_dt
            )
            sess.add(comp)
            await sess.commit()
            print(f"   ✓ Stored in CompletedOccurrence")
            print()
            
            # Now change the recurrence rule - every Tuesday instead
            new_text = 'Team standup every Tuesday'
            new_dtstart, new_rrule = parse_text_to_rrule_string(new_text)
            
            todo.text = new_text
            todo.recurrence_rrule = new_rrule
            todo.recurrence_dtstart = new_dtstart
            sess.add(todo)
            await sess.commit()
            
            print(f"4. Changed recurring todo: '{todo.text}'")
            print(f"   New RRULE: {new_rrule}")
            print(f"   New DTSTART: {new_dtstart}")
            print()
            
            # Query calendar again
            result2 = await calendar_occurrences(
                mock_request,
                start=start.isoformat(),
                end=end.isoformat(),
                current_user=MockUser()
            )
            
            new_occs = [o for o in result2.get('occurrences', []) if o.get('id') == todo.id]
            print(f"5. New occurrences in October (after rule change):")
            for o in new_occs[:5]:
                dt = datetime.fromisoformat(o['occurrence_dt'].replace('Z', '+00:00'))
                completed_mark = " ✓ COMPLETED" if o.get('completed') else ""
                print(f"   - {dt.strftime('%a %b %d')} ({o['occurrence_dt']}){completed_mark}")
            print()
            
            # Check if the old completed date appears anywhere
            completed_date = comp_dt.date()
            matching = [o for o in result2.get('occurrences', []) 
                       if datetime.fromisoformat(o['occurrence_dt'].replace('Z', '+00:00')).date() == completed_date]
            
            print(f"6. Looking for completed date {completed_date.strftime('%a %b %d')}:")
            if matching:
                for m in matching:
                    print(f"   Found: item_id={m['id']}, completed={m.get('completed')}")
                    if m['id'] == todo.id:
                        if m.get('completed'):
                            print(f"   ✅ YES! The old completed occurrence still shows as completed!")
                        else:
                            print(f"   ❌ NO! The occurrence exists but is NOT marked completed")
            else:
                print(f"   ❌ NO occurrences found for {completed_date}")
                print()
                print(f"   This is the problem:")
                print(f"   - Old rule generated Monday Oct 13")
                print(f"   - New rule generates Tuesdays only")
                print(f"   - Monday Oct 13 is NO LONGER GENERATED")
                print(f"   - So the completion record exists but is 'orphaned'")
                print(f"   - No way to display it in the calendar!")
            
            # Cleanup
            await sess.delete(comp)
            await sess.delete(todo)
            await sess.commit()
        else:
            print("Not enough occurrences to test")
            await sess.delete(todo)
            await sess.commit()
    
    print()
    print("=" * 70)


def test_rrule_change():
    asyncio.run(_async_test_rrule_change())

if __name__ == '__main__':
    asyncio.run(_async_test_rrule_change())
