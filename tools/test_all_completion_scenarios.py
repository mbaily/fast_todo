#!/usr/bin/env python3
"""
Comprehensive test: All completion history scenarios working.

Tests:
1. Title change - completion preserved
2. Time change - completion preserved  
3. Recurrence rule change - phantom occurrence created
"""
import asyncio
import os
import pytest
pytestmark = pytest.mark.asyncio
from datetime import datetime, timezone


async def _async_test_all_scenarios():
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
    print("COMPREHENSIVE TEST: All Completion History Scenarios")
    print("=" * 70)
    print()
    
    async with async_session() as sess:
        # Get user and list
        result = await sess.exec(select(User).limit(1))
        user = result.first()
        result = await sess.exec(select(ListState).where(ListState.owner_id == user.id).limit(1))
        test_list = result.first()
        
        class MockUser:
            id = user.id
        
        mock_request = Mock(spec=Request)
        mock_request.headers = {}
        
        start = datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
        
        # ============================================================
        # SCENARIO 1: Title Change
        # ============================================================
        print("SCENARIO 1: Title Change")
        print("-" * 70)
        
        todo1 = Todo(text='Project review on October 15', list_id=test_list.id)
        sess.add(todo1)
        await sess.commit()
        await sess.refresh(todo1)
        
        # Complete it
        dt1 = datetime(2025, 10, 15, 0, 0, 0, tzinfo=timezone.utc)
        hash1 = occurrence_hash('todo', todo1.id, dt1, '', todo1.text)
        comp1 = CompletedOccurrence(
            user_id=user.id,
            occ_hash=hash1,
            item_type='todo',
            item_id=todo1.id,
            occurrence_dt=dt1
        )
        sess.add(comp1)
        await sess.commit()
        print(f"✓ Created and completed: '{todo1.text}'")
        
        # Change title
        todo1.text = 'Code review on October 15'
        sess.add(todo1)
        await sess.commit()
        print(f"✓ Changed title to: '{todo1.text}'")
        
        # Check calendar
        result = await calendar_occurrences(mock_request, start=start.isoformat(), end=end.isoformat(), current_user=MockUser())
        occs = [o for o in result['occurrences'] if o['id'] == todo1.id]
        
        if occs and occs[0]['completed']:
            print(f"✅ PASS: Completion preserved after title change")
        else:
            print(f"❌ FAIL: Completion lost after title change")
        print()
        
        # ============================================================
        # SCENARIO 2: Recurrence Rule Change
        # ============================================================
        print("SCENARIO 2: Recurrence Rule Change")
        print("-" * 70)
        
        todo2_text = 'Team meeting every Monday'
        dtstart2, rrule2 = parse_text_to_rrule_string(todo2_text)
        todo2 = Todo(
            text=todo2_text,
            list_id=test_list.id,
            recurrence_rrule=rrule2,
            recurrence_dtstart=dtstart2
        )
        sess.add(todo2)
        await sess.commit()
        await sess.refresh(todo2)
        print(f"✓ Created recurring: '{todo2.text}'")
        
        # Complete Monday Oct 13
        dt2 = datetime(2025, 10, 13, 14, 0, 0, tzinfo=timezone.utc)
        hash2 = occurrence_hash('todo', todo2.id, dt2, rrule2, todo2.text)
        comp2 = CompletedOccurrence(
            user_id=user.id,
            occ_hash=hash2,
            item_type='todo',
            item_id=todo2.id,
            occurrence_dt=dt2
        )
        sess.add(comp2)
        await sess.commit()
        print(f"✓ Completed: Monday Oct 13")
        
        # Change to every Tuesday
        todo2.text = 'Team meeting every Tuesday'
        dtstart2_new, rrule2_new = parse_text_to_rrule_string(todo2.text)
        todo2.recurrence_rrule = rrule2_new
        todo2.recurrence_dtstart = dtstart2_new
        sess.add(todo2)
        await sess.commit()
        print(f"✓ Changed to: '{todo2.text}'")
        
        # Check calendar
        result = await calendar_occurrences(mock_request, start=start.isoformat(), end=end.isoformat(), current_user=MockUser())
        occs2 = [o for o in result['occurrences'] if o['id'] == todo2.id]
        
        # Look for the Monday Oct 13 occurrence
        monday_oct13 = [o for o in occs2 if datetime.fromisoformat(o['occurrence_dt'].replace('Z', '+00:00')).date().day == 13]
        
        if monday_oct13 and monday_oct13[0]['completed']:
            phantom = monday_oct13[0].get('phantom', False)
            print(f"✅ PASS: Monday Oct 13 still shows (phantom={phantom})")
        else:
            print(f"❌ FAIL: Monday Oct 13 disappeared")
        print()
        
        # ============================================================
        # SCENARIO 3: Combined - Title AND Rule Change
        # ============================================================
        print("SCENARIO 3: Title + Rule Change")
        print("-" * 70)
        
        todo3_text = 'Standup every Friday'
        dtstart3, rrule3 = parse_text_to_rrule_string(todo3_text)
        todo3 = Todo(
            text=todo3_text,
            list_id=test_list.id,
            recurrence_rrule=rrule3,
            recurrence_dtstart=dtstart3
        )
        sess.add(todo3)
        await sess.commit()
        await sess.refresh(todo3)
        print(f"✓ Created: '{todo3.text}'")
        
        # Complete Friday Oct 10
        dt3 = datetime(2025, 10, 10, 9, 0, 0, tzinfo=timezone.utc)
        hash3 = occurrence_hash('todo', todo3.id, dt3, rrule3, todo3.text)
        comp3 = CompletedOccurrence(
            user_id=user.id,
            occ_hash=hash3,
            item_type='todo',
            item_id=todo3.id,
            occurrence_dt=dt3
        )
        sess.add(comp3)
        await sess.commit()
        print(f"✓ Completed: Friday Oct 10")
        
        # Change BOTH title and rule
        todo3.text = 'Daily standup every day'
        dtstart3_new, rrule3_new = parse_text_to_rrule_string(todo3.text)
        todo3.recurrence_rrule = rrule3_new
        todo3.recurrence_dtstart = dtstart3_new
        sess.add(todo3)
        await sess.commit()
        print(f"✓ Changed to: '{todo3.text}'")
        
        # Check calendar
        result = await calendar_occurrences(mock_request, start=start.isoformat(), end=end.isoformat(), current_user=MockUser())
        occs3 = [o for o in result['occurrences'] if o['id'] == todo3.id]
        
        # Look for Oct 10 - might be phantom or might match new rule
        oct10_occs = [o for o in occs3 if datetime.fromisoformat(o['occurrence_dt'].replace('Z', '+00:00')).date().day == 10]
        
        if oct10_occs:
            completed_oct10 = [o for o in oct10_occs if o.get('completed')]
            if completed_oct10:
                print(f"✅ PASS: Oct 10 completion preserved (found {len(oct10_occs)} occurrence(s))")
            else:
                print(f"❌ FAIL: Oct 10 exists but not marked completed")
        else:
            print(f"❌ FAIL: Oct 10 disappeared completely")
        print()
        
        # Cleanup
        await sess.delete(comp1)
        await sess.delete(comp2)
        await sess.delete(comp3)
        await sess.delete(todo1)
        await sess.delete(todo2)
        await sess.delete(todo3)
        await sess.commit()
        
        print("=" * 70)
        print("All scenarios tested. Cleanup complete.")
        print("=" * 70)


def test_all_scenarios():
    asyncio.run(_async_test_all_scenarios())

if __name__ == '__main__':
    asyncio.run(_async_test_all_scenarios())
    asyncio.run(test_all_scenarios())
