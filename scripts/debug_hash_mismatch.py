#!/usr/bin/env python3
"""
Debug script to reproduce the hash calculation and see why stored hashes
don't match generated hashes for todos 549, 392, 397.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from datetime import datetime, timezone
from app.utils import occurrence_hash
import sqlite3
import json

def main():
    conn = sqlite3.connect('fast_todo.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    todo_ids = [549, 392, 397]
    
    for todo_id in todo_ids:
        print(f'\n=== TODO {todo_id} ===')
        
        # Get todo details
        cursor.execute('SELECT text, recurrence_rrule, recurrence_dtstart FROM todo WHERE id = ?', (todo_id,))
        todo = cursor.fetchone()
        if not todo:
            print('Not found')
            continue
        
        current_title = todo['text']
        current_rrule = todo['recurrence_rrule']
        print(f'Current title: {current_title}')
        print(f'Current RRULE: {current_rrule}')
        
        # Get completed occurrence
        cursor.execute('''
            SELECT occurrence_dt, occ_hash, metadata_json
            FROM completedoccurrence
            WHERE item_type = 'todo' AND item_id = ?
        ''', (todo_id,))
        
        comp = cursor.fetchone()
        if not comp:
            print('No completed occurrence')
            continue
        
        stored_hash = comp['occ_hash']
        occ_dt_str = comp['occurrence_dt']
        
        # Parse metadata to get stored title
        stored_title = current_title  # default
        if comp['metadata_json']:
            try:
                meta = json.loads(comp['metadata_json'])
                stored_title = meta.get('title', current_title)
            except:
                pass
        
        print(f'Stored hash: {stored_hash}')
        print(f'Stored title (from metadata): {stored_title}')
        print(f'Occurrence dt: {occ_dt_str}')
        
        # Parse the occurrence_dt
        occ_dt = datetime.fromisoformat(occ_dt_str.replace('Z', '+00:00'))
        if occ_dt.tzinfo is None:
            occ_dt = occ_dt.replace(tzinfo=timezone.utc)
        
        # Compute hash using STORED title (what was used at completion time)
        hash_with_stored = occurrence_hash('todo', todo_id, occ_dt, current_rrule, stored_title)
        print(f'\nHash with STORED title: {hash_with_stored}')
        print(f'Matches stored hash? {hash_with_stored == stored_hash}')
        
        # Compute hash using CURRENT title (what would be generated now)
        hash_with_current = occurrence_hash('todo', todo_id, occ_dt, current_rrule, current_title)
        print(f'\nHash with CURRENT title: {hash_with_current}')
        print(f'Matches stored hash? {hash_with_current == stored_hash}')
        
        # Also try with the RRULE that might have been there at completion time
        # (Check if there was a rrule stored in metadata)
        stored_rrule = current_rrule
        if comp['metadata_json']:
            try:
                meta = json.loads(comp['metadata_json'])
                if 'rrule' in meta:
                    stored_rrule = meta.get('rrule', current_rrule)
            except:
                pass
        
        if stored_rrule != current_rrule:
            print(f'\nRRULE changed! Stored: {stored_rrule}, Current: {current_rrule}')
            hash_with_stored_rrule = occurrence_hash('todo', todo_id, occ_dt, stored_rrule, stored_title)
            print(f'Hash with STORED rrule: {hash_with_stored_rrule}')
            print(f'Matches stored hash? {hash_with_stored_rrule == stored_hash}')
    
    conn.close()

if __name__ == '__main__':
    main()
