#!/usr/bin/env python3
"""
Comprehensive calendar test - tests the entire calendar workflow.
This test:
1. Creates a fresh database with a test user
2. Creates todos with dates and recurrence rules
3. Tests calendar occurrences generation
4. Tests completion/uncommit
5. Tests ignore functionality
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__) + '/..')

import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
import shutil

# Import after path setup
from app.db import async_session, engine
from app.models import User, Todo, ListState, IgnoredScope, CompletedOccurrence
from sqlmodel import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

async def setup_fresh_database():
    """Backup existing DB and create a fresh one."""
    db_path = Path('fast_todo.db')
    
    if db_path.exists():
        backup_path = Path(f'fast_todo.db.backup.{datetime.now().strftime("%Y%m%d_%H%M%S")}')
        print(f"📦 Backing up existing database to {backup_path}")
        shutil.copy2(db_path, backup_path)
        db_path.unlink()
        print(f"🗑️  Deleted {db_path}")
    
    # Initialize schema using init_db
    from app.db import init_db
    await init_db()
    print("✅ Created fresh database with schema")

async def create_test_user():
    """Create a test user."""
    from app.auth import pwd_context
    
    async with async_session() as sess:
        # Create user
        user = User(
            username="testuser",
            password_hash=pwd_context.hash("testpass123")
        )
        sess.add(user)
        await sess.commit()
        await sess.refresh(user)
        
        # Create default list
        default_list = ListState(
            name="My Tasks",
            owner_id=user.id
        )
        sess.add(default_list)
        await sess.commit()
        await sess.refresh(default_list)
        
        print(f"✅ Created test user: {user.username} (id={user.id})")
        print(f"✅ Created default list: {default_list.name} (id={default_list.id})")
        
        return user, default_list

async def create_test_todos(list_id: int):
    """Create test todos with various date patterns."""
    async with async_session() as sess:
        todos_data = [
            # Single date todos
            ("Buy groceries on 2025-10-05", False, None),
            ("Doctor appointment 2025-10-12 3pm", False, None),
            ("Pay rent on 2025-10-15", False, None),
            ("Car service 2025-10-22", False, None),
            
            # Recurrence todos (plain English)
            ("Water plants every Monday", False, "FREQ=WEEKLY;BYDAY=MO"),
            ("Team meeting every Tuesday at 10am", False, "FREQ=WEEKLY;BYDAY=TU"),
            ("Trash out every Thursday", False, "FREQ=WEEKLY;BYDAY=TH"),
            ("Gym workout every 2 days", False, "FREQ=DAILY;INTERVAL=2"),
            ("Monthly report on the 1st", False, "FREQ=MONTHLY;BYMONTHDAY=1"),
            
            # Edge cases
            ("Call mom 2025-10-05 and 2025-10-20", False, None),  # Multiple dates
        ]
        
        created_todos = []
        for text, calendar_ignored, rrule in todos_data:
            todo = Todo(
                text=text,
                list_id=list_id,
                calendar_ignored=calendar_ignored,
                recurrence_rrule=rrule,
                recurrence_dtstart=datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc) if rrule else None
            )
            sess.add(todo)
            created_todos.append((text, rrule))
        
        await sess.commit()
        
        print(f"\n✅ Created {len(todos_data)} test todos:")
        for text, rrule in created_todos:
            print(f"  - {text[:60]} {f'(rrule: {rrule})' if rrule else ''}")
        
        # Return list of created todo texts for verification
        return [text for text, _ in created_todos]

async def caltest_calendar_occurrences(user_id: int):
    """Test calendar occurrences generation."""
    from app.main import calendar_occurrences
    from unittest.mock import Mock
    
    print(f"\n{'='*80}")
    print("TEST 1: Calendar Occurrences Generation")
    print('='*80)
    
    mock_request = Mock()
    mock_request.query_params = Mock()
    mock_request.query_params.get = Mock(return_value=None)
    
    mock_user = Mock()
    mock_user.id = user_id
    mock_user.username = "testuser"
    
    start = datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2025, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
    
    result = await calendar_occurrences(
        mock_request,
        start=start.isoformat(),
        end=end.isoformat(),
        current_user=mock_user
    )
    
    occs = result.get('occurrences', [])
    print(f"\n✅ Generated {len(occs)} occurrences for October 2025")
    
    # Group by todo
    by_todo = {}
    for occ in occs:
        todo_id = occ.get('id')
        if todo_id not in by_todo:
            by_todo[todo_id] = []
        by_todo[todo_id].append(occ)
    
    print(f"\n📊 Occurrences by todo:")
    for todo_id, todo_occs in sorted(by_todo.items()):
        title = todo_occs[0].get('title', '')[:50]
        print(f"  Todo {todo_id}: {len(todo_occs)} occurrence(s) - {title}")
        for occ in todo_occs[:3]:  # Show first 3
            print(f"    - {occ.get('occurrence_dt')}")
        if len(todo_occs) > 3:
            print(f"    ... and {len(todo_occs) - 3} more")
    
    # Check for unexpected patterns
    issues = []
    
    # Check for todos with no occurrences (might be OK if dates are outside range)
    async with async_session() as sess:
        all_todos_result = await sess.exec(select(Todo))
        all_todos = all_todos_result.all()
        
        for todo in all_todos:
            if todo.id not in by_todo:
                print(f"  ⚠️  Todo {todo.id} has no occurrences: {todo.text[:50]}")
    
    return len(occs), by_todo

async def caltest_complete_occurrence(user_id: int, by_todo: dict):
    """Test completing and uncompleting an occurrence."""
    print(f"\n{'='*80}")
    print("TEST 2: Complete/Uncomplete Occurrence")
    print('='*80)
    
    # Pick first occurrence
    if not by_todo:
        print("❌ No occurrences to test with!")
        return False
    
    todo_id = list(by_todo.keys())[0]
    occ = by_todo[todo_id][0]
    
    item_type = occ.get('item_type')
    item_id = occ.get('id')
    occ_dt_str = occ.get('occurrence_dt')
    
    print(f"\n📝 Testing with occurrence:")
    print(f"  Todo ID: {item_id}")
    print(f"  Title: {occ.get('title')}")
    print(f"  Date: {occ_dt_str}")
    
    # Parse occurrence datetime
    from dateutil.parser import parse as parse_date
    occ_dt = parse_date(occ_dt_str)
    
    # Create completion
    # Phase 1 calendar completion model uses CompletedOccurrence rows, not TodoCompletion.
    async with async_session() as sess:
        completion = CompletedOccurrence(
            user_id=user_id,
            item_type=item_type,
            item_id=item_id,
            occurrence_dt=occ_dt,
            occ_hash=None,
            metadata_json=None
        )
        sess.add(completion)
        await sess.commit()
        await sess.refresh(completion)
        print(f"\n✅ Created completion (CompletedOccurrence id={completion.id})")
    
    # Verify completion shows in calendar
    from app.main import calendar_occurrences
    from unittest.mock import Mock
    
    mock_request = Mock()
    mock_request.query_params = Mock()
    mock_request.query_params.get = Mock(return_value=None)
    
    mock_user = Mock()
    mock_user.id = user_id
    mock_user.username = "testuser"
    
    start = datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2025, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
    
    result = await calendar_occurrences(
        mock_request,
        start=start.isoformat(),
        end=end.isoformat(),
        current_user=mock_user
    )
    
    # Find the occurrence we completed
    completed_occ = None
    for o in result.get('occurrences', []):
        if (o.get('id') == item_id and 
            o.get('occurrence_dt') == occ_dt_str):
            completed_occ = o
            break
    
    if completed_occ and completed_occ.get('completed'):
        print(f"✅ Occurrence shows as completed in calendar")
    else:
        print(f"❌ Occurrence NOT showing as completed!")
        return False
    
    # Uncomplete
    async with async_session() as sess:
        result = await sess.exec(
            select(CompletedOccurrence).where(
                CompletedOccurrence.id == completion.id
            )
        )
        comp = result.first()
        if comp:
            await sess.delete(comp)
            await sess.commit()
            print(f"✅ Deleted completion (CompletedOccurrence)")
    
    # Verify uncomplete shows in calendar
    result = await calendar_occurrences(
        mock_request,
        start=start.isoformat(),
        end=end.isoformat(),
        current_user=mock_user
    )
    
    uncompleted_occ = None
    for o in result.get('occurrences', []):
        if (o.get('id') == item_id and 
            o.get('occurrence_dt') == occ_dt_str):
            uncompleted_occ = o
            break
    
    if uncompleted_occ and not uncompleted_occ.get('completed'):
        print(f"✅ Occurrence shows as uncompleted in calendar")
        return True
    else:
        print(f"❌ Occurrence NOT showing as uncompleted!")
        return False

async def caltest_ignore_functionality(user_id: int, by_todo: dict):
    """Test ignore and ignore_from functionality."""
    print(f"\n{'='*80}")
    print("TEST 3: Ignore Functionality")
    print('='*80)
    
    if len(by_todo) < 2:
        print("❌ Need at least 2 todos to test ignore!")
        return False
    
    # Test 1: calendar_ignored flag
    todo_ids = list(by_todo.keys())
    test_todo_id = todo_ids[0]
    
    print(f"\n📝 Test 3a: calendar_ignored flag")
    print(f"  Testing with todo {test_todo_id}")
    
    async with async_session() as sess:
        todo = await sess.get(Todo, test_todo_id)
        if todo:
            todo.calendar_ignored = True
            sess.add(todo)
            await sess.commit()
            print(f"✅ Set calendar_ignored=True for todo {test_todo_id}")
    
    # Verify doesn't show without include_ignored
    from app.main import calendar_occurrences
    from unittest.mock import Mock
    
    mock_request = Mock()
    mock_request.query_params = Mock()
    mock_request.query_params.get = Mock(return_value=None)
    
    mock_user = Mock()
    mock_user.id = user_id
    mock_user.username = "testuser"
    
    start = datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2025, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
    
    result = await calendar_occurrences(
        mock_request,
        start=start.isoformat(),
        end=end.isoformat(),
        current_user=mock_user
    )
    
    ignored_found = any(o.get('id') == test_todo_id for o in result.get('occurrences', []))
    
    if not ignored_found:
        print(f"✅ calendar_ignored todo correctly hidden")
    else:
        print(f"❌ calendar_ignored todo still showing!")
        return False
    
    # Verify shows with include_ignored
    mock_request.query_params.get = Mock(side_effect=lambda k, default=None: 
        '1' if k == 'include_ignored' else default)
    
    result = await calendar_occurrences(
        mock_request,
        start=start.isoformat(),
        end=end.isoformat(),
        current_user=mock_user,
        include_ignored=True
    )
    
    ignored_occs = [o for o in result.get('occurrences', []) 
                    if o.get('id') == test_todo_id]
    
    if ignored_occs and ignored_occs[0].get('ignored'):
        print(f"✅ calendar_ignored todo shows with include_ignored=True")
        print(f"  Marked with scopes: {ignored_occs[0].get('ignored_scopes')}")
    else:
        print(f"❌ calendar_ignored todo not showing with include_ignored!")
        return False
    
    # Test 2: todo_from scope
    print(f"\n📝 Test 3b: todo_from ignore scope")
    test_todo_id_2 = todo_ids[1]
    test_occs = by_todo[test_todo_id_2]
    
    if len(test_occs) < 2:
        print(f"⚠️  Todo {test_todo_id_2} only has {len(test_occs)} occurrence(s), skipping ignore_from test")
    else:
        # Ignore from middle occurrence
        mid_idx = len(test_occs) // 2
        from_occ = test_occs[mid_idx]
        from_dt_str = from_occ.get('occurrence_dt')
        from dateutil.parser import parse as parse_date
        from_dt = parse_date(from_dt_str)
        
        print(f"  Testing ignore_from for todo {test_todo_id_2}")
        print(f"  Ignoring from date: {from_dt_str}")
        
        from app.utils import ignore_todo_from_hash
        scope_hash = ignore_todo_from_hash(str(test_todo_id_2), from_dt)
        
        async with async_session() as sess:
            ignore_scope = IgnoredScope(
                user_id=user_id,
                scope_type='todo_from',
                scope_key=str(test_todo_id_2),
                scope_hash=scope_hash,
                from_dt=from_dt
            )
            sess.add(ignore_scope)
            await sess.commit()
            print(f"✅ Created todo_from ignore scope")
        
        # Verify occurrences before from_dt still show, after don't
        mock_request.query_params.get = Mock(return_value=None)
        
        result = await calendar_occurrences(
            mock_request,
            start=start.isoformat(),
            end=end.isoformat(),
            current_user=mock_user
        )
        
        remaining_occs = [o for o in result.get('occurrences', []) 
                         if o.get('id') == test_todo_id_2]
        
        print(f"  Before ignore: {len(test_occs)} occurrences")
        print(f"  After ignore: {len(remaining_occs)} occurrences")
        
        # Check that all remaining are before from_dt
        all_before = all(
            parse_date(o.get('occurrence_dt')) < from_dt
            for o in remaining_occs
        )
        
        if all_before and len(remaining_occs) < len(test_occs):
            print(f"✅ todo_from correctly filtered occurrences")
        else:
            print(f"❌ todo_from filtering not working correctly!")
            return False
    
    return True

async def run_comprehensive_test():
    """Run the complete test suite."""
    print(f"\n{'#'*80}")
    print("# COMPREHENSIVE CALENDAR TEST")
    print(f"{'#'*80}\n")
    
    try:
        # Step 1: Fresh database
        print("STEP 1: Setting up fresh database")
        print("-" * 80)
        await setup_fresh_database()
        
        # Step 2: Create user
        print(f"\nSTEP 2: Creating test user")
        print("-" * 80)
        user, default_list = await create_test_user()
        
        # Step 3: Create test todos
        print(f"\nSTEP 3: Creating test todos")
        print("-" * 80)
        todo_texts = await create_test_todos(default_list.id)
        
        # Step 4: Test calendar generation
        print(f"\nSTEP 4: Testing calendar occurrences")
        print("-" * 80)
        occ_count, by_todo = await caltest_calendar_occurrences(user.id)
        
        if occ_count == 0:
            print("\n❌ FAILED: No occurrences generated!")
            return False
        
        # Step 5: Test completion
        print(f"\nSTEP 5: Testing completion/uncomplete")
        print("-" * 80)
        completion_ok = await caltest_complete_occurrence(user.id, by_todo)
        
        if not completion_ok:
            print("\n❌ FAILED: Completion test failed!")
            return False
        
        # Step 6: Test ignore functionality
        print(f"\nSTEP 6: Testing ignore functionality")
        print("-" * 80)
        ignore_ok = await caltest_ignore_functionality(user.id, by_todo)
        
        if not ignore_ok:
            print("\n❌ FAILED: Ignore test failed!")
            return False
        
        # Summary
        print(f"\n{'#'*80}")
        print("# TEST SUMMARY")
        print(f"{'#'*80}")
        print(f"✅ Database setup: PASSED")
        print(f"✅ User creation: PASSED")
        print(f"✅ Todo creation: PASSED ({len(todo_texts)} todos)")
        print(f"✅ Calendar generation: PASSED ({occ_count} occurrences)")
        print(f"✅ Completion test: PASSED")
        print(f"✅ Ignore functionality: PASSED")
        print(f"\n{'🎉'*20}")
        print("ALL TESTS PASSED!")
        print(f"{'🎉'*20}\n")
        
        return True
        
    except Exception as e:
        print(f"\n{'='*80}")
        print(f"❌ TEST FAILED WITH EXCEPTION:")
        print(f"{'='*80}")
        print(f"{type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = asyncio.run(run_comprehensive_test())
    sys.exit(0 if success else 1)
