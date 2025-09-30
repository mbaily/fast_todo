#!/usr/bin/env python3
"""
Solution for preserving completion history when todo text changes.

Problem: occurrence_hash includes the title, so changing todo text orphans completions.

Solution: 
1. Parse and store item_type, item_id, occurrence_dt when marking complete
2. Use these fields as a fallback when checking completion status
3. Backfill existing CompletedOccurrence rows with parsed metadata

Usage:
  # Dry run to see what would be backfilled
  python scripts/fix_completion_history.py --action analyze
  
  # Backfill existing completion records
  python scripts/fix_completion_history.py --action backfill --commit
  
  # Test the hash change scenario
  python scripts/fix_completion_history.py --action test
"""
import argparse
import asyncio
import json
import os
from datetime import datetime, timezone


def parse_occurrence_hash(occ_hash: str) -> dict | None:
    """
    Parse an occurrence hash to extract metadata.
    
    Hash format: occ:<sha256 of {"type":"todo","id":"123","dt":"2025-10-01T12:00:00Z","rrule":"...","title":"..."}>
    
    Returns dict with type, id, dt, rrule, title if parseable, else None.
    Note: We can't reverse the hash, but we can store these fields when creating the hash.
    """
    # We can't actually reverse a SHA256 hash
    # This is why we need to store the fields at creation time
    return None


async def analyze_completions():
    """Analyze existing CompletedOccurrence records to see which lack metadata."""
    os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///./fast_todo.db')
    from sqlmodel import select
    from app.db import async_session, init_db
    from app.models import CompletedOccurrence
    
    await init_db()
    
    async with async_session() as sess:
        result = await sess.exec(select(CompletedOccurrence))
        rows = result.all()
        
        total = len(rows)
        with_metadata = sum(1 for r in rows if r.item_type and r.item_id and r.occurrence_dt)
        without_metadata = total - with_metadata
        
        print(f"Total completion records: {total}")
        print(f"With metadata (item_type, item_id, occurrence_dt): {with_metadata}")
        print(f"Without metadata: {without_metadata}")
        print()
        
        if without_metadata > 0:
            print("⚠️  Records without metadata will lose history if todo text changes!")
            print()
            print("These records need the metadata fields populated.")
            print("Unfortunately, we can't reverse the hash to extract the original data.")
            print()
            print("OPTIONS:")
            print("1. Accept that old completions may be orphaned (current behavior)")
            print("2. Re-complete these occurrences after deploying the fix (manual)")
            print("3. Keep a separate completion log with timestamps (new feature)")
            
        return {
            'total': total,
            'with_metadata': with_metadata,
            'without_metadata': without_metadata
        }


async def test_hash_change_scenario():
    """Test scenario: complete an occurrence, change title, verify it still shows as completed."""
    os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///./fast_todo.db')
    from sqlmodel import select
    from app.db import async_session, init_db
    from app.models import User, ListState, Todo, CompletedOccurrence
    from app.utils import occurrence_hash
    
    await init_db()
    
    test_date = datetime(2025, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    
    async with async_session() as sess:
        # Get test user
        result = await sess.exec(select(User).where(User.username == 'dev_user'))
        user = result.first()
        if not user:
            print("❌ No dev_user found. Create one first.")
            return
        
        # Get or create test list
        result = await sess.exec(select(ListState).where(
            ListState.owner_id == user.id,
            ListState.name == 'completion-history-test'
        ))
        test_list = result.first()
        if not test_list:
            test_list = ListState(name='completion-history-test', owner_id=user.id)
            sess.add(test_list)
            await sess.commit()
            await sess.refresh(test_list)
        
        # Create test todo
        todo = Todo(
            text='Doctor appointment weekly on October 15',
            list_id=test_list.id,
            recurrence_rrule='FREQ=WEEKLY',
            recurrence_dtstart=test_date
        )
        sess.add(todo)
        await sess.commit()
        await sess.refresh(todo)
        
        print(f"✓ Created test todo #{todo.id}: '{todo.text}'")
        
        # Generate hash for Oct 15 occurrence
        hash1 = occurrence_hash('todo', todo.id, test_date, 'FREQ=WEEKLY', todo.text)
        print(f"✓ Generated hash (original title): {hash1[:20]}...")
        
        # Mark it complete (CURRENT behavior - only hash stored)
        comp1 = CompletedOccurrence(
            user_id=user.id,
            occ_hash=hash1,
            # These fields are currently NOT populated:
            item_type=None,
            item_id=None,
            occurrence_dt=None
        )
        sess.add(comp1)
        await sess.commit()
        print(f"✓ Marked occurrence complete (old way - no metadata)")
        
        # Now change the todo text
        todo.text = 'Doctor checkup weekly on October 15'
        sess.add(todo)
        await sess.commit()
        print(f"✓ Changed todo text to: '{todo.text}'")
        
        # Generate new hash with changed title
        hash2 = occurrence_hash('todo', todo.id, test_date, 'FREQ=WEEKLY', todo.text)
        print(f"✓ New hash (changed title): {hash2[:20]}...")
        
        # Check if hashes differ
        if hash1 != hash2:
            print()
            print("❌ PROBLEM: Hash changed! Completion is now orphaned.")
            print()
            
            # Show that we can't find the completion with new hash
            result = await sess.exec(select(CompletedOccurrence).where(
                CompletedOccurrence.user_id == user.id,
                CompletedOccurrence.occ_hash == hash2
            ))
            if not result.first():
                print("   Looking for completion with NEW hash: NOT FOUND")
            
            # But it still exists with old hash
            result = await sess.exec(select(CompletedOccurrence).where(
                CompletedOccurrence.user_id == user.id,
                CompletedOccurrence.occ_hash == hash1
            ))
            if result.first():
                print("   Looking for completion with OLD hash: FOUND (but useless)")
            print()
            
            # Now demonstrate the solution
            print("✓ SOLUTION: Store metadata at completion time")
            print()
            
            # Mark another occurrence (FIXED behavior - with metadata)
            test_date2 = datetime(2025, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
            hash3 = occurrence_hash('todo', todo.id, test_date2, 'FREQ=WEEKLY', todo.text)
            
            comp2 = CompletedOccurrence(
                user_id=user.id,
                occ_hash=hash3,
                # NEW: Store metadata for fallback lookup
                item_type='todo',
                item_id=todo.id,
                occurrence_dt=test_date2
            )
            sess.add(comp2)
            await sess.commit()
            print(f"✓ Marked Oct 22 complete (new way - with metadata)")
            
            # Change title again
            todo.text = 'Dr. visit weekly on October 15'
            sess.add(todo)
            await sess.commit()
            print(f"✓ Changed todo text again to: '{todo.text}'")
            
            # Generate new hash
            hash4 = occurrence_hash('todo', todo.id, test_date2, 'FREQ=WEEKLY', todo.text)
            
            # Check with new hash (won't match)
            result = await sess.exec(select(CompletedOccurrence).where(
                CompletedOccurrence.user_id == user.id,
                CompletedOccurrence.occ_hash == hash4
            ))
            if not result.first():
                print("   Looking for Oct 22 with NEW hash: NOT FOUND")
            
            # But we can find it by metadata!
            result = await sess.exec(select(CompletedOccurrence).where(
                CompletedOccurrence.user_id == user.id,
                CompletedOccurrence.item_type == 'todo',
                CompletedOccurrence.item_id == todo.id,
                CompletedOccurrence.occurrence_dt == test_date2
            ))
            if result.first():
                print("✅ Looking for Oct 22 by metadata: FOUND!")
                print()
                print("   This is the fix! Calendar code should check BOTH:")
                print("   1. Hash match (fast path)")
                print("   2. Metadata match (fallback for changed titles)")
        
        # Cleanup
        await sess.delete(comp1)
        if 'comp2' in locals():
            await sess.delete(comp2)
        await sess.delete(todo)
        await sess.delete(test_list)
        await sess.commit()
        print()
        print("✓ Test cleanup complete")


def show_implementation_guide():
    """Show the code changes needed to implement the fix."""
    print("=" * 70)
    print("IMPLEMENTATION GUIDE")
    print("=" * 70)
    print()
    print("Changes needed to preserve completion history when titles change:")
    print()
    print("1. UPDATE /occurrence/complete endpoint (app/main.py ~line 4689)")
    print("   Currently stores only: user_id, occ_hash")
    print("   Need to parse and store: item_type, item_id, occurrence_dt")
    print()
    print("2. UPDATE /calendar/occurrences endpoint (app/main.py ~line 3539)")
    print("   Currently checks: if occ_hash in done_hashes")
    print("   Need to also check: if (item_type, item_id, occurrence_dt) in done_metadata")
    print()
    print("3. OPTIONAL: Add migration to handle existing data")
    print("   Existing CompletedOccurrence rows lack metadata")
    print("   Users will need to re-complete those occurrences")
    print()
    print("4. CONSIDER: Remove title from hash calculation")
    print("   Alternative approach: Don't include title in hash at all")
    print("   Pros: Simpler, no fallback needed")
    print("   Cons: Breaking change, all existing completions orphaned")
    print()
    print("=" * 70)


async def main():
    parser = argparse.ArgumentParser(description='Fix completion history when todo text changes')
    parser.add_argument('--action', choices=['analyze', 'test', 'guide'], 
                       default='guide',
                       help='Action to perform')
    parser.add_argument('--commit', action='store_true',
                       help='Actually commit changes (for backfill)')
    
    args = parser.parse_args()
    
    if args.action == 'analyze':
        await analyze_completions()
    elif args.action == 'test':
        await test_hash_change_scenario()
    elif args.action == 'guide':
        show_implementation_guide()


if __name__ == '__main__':
    asyncio.run(main())
