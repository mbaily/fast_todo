#!/usr/bin/env python3
"""
Simulate generating occurrences for todos 549 and 392 to see what's being sent to client.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from datetime import datetime, timezone
from app.utils import occurrence_hash
import sqlite3

def main():
    conn = sqlite3.connect('fast_todo.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    todo_ids = [549, 392]
    
    for todo_id in todo_ids:
        print(f'\n=== TODO {todo_id} ===')
        
        cursor.execute('SELECT text, recurrence_rrule FROM todo WHERE id = ?', (todo_id,))
        todo = cursor.fetchone()
        if not todo:
            continue
        
        title = todo['text']
        rrule = todo['recurrence_rrule']
        
        print(f'Title: {title}')
        print(f'RRULE: {rrule}')
        
        # Get the completed occurrence date
        cursor.execute('''
            SELECT occurrence_dt, occ_hash
            FROM completedoccurrence
            WHERE item_type = 'todo' AND item_id = ?
        ''', (todo_id,))
        comp = cursor.fetchone()
        if not comp:
            continue
        
        occ_dt_str = comp['occurrence_dt']
        stored_hash = comp['occ_hash']
        
        occ_dt = datetime.fromisoformat(occ_dt_str.replace('Z', '+00:00'))
        if occ_dt.tzinfo is None:
            occ_dt = occ_dt.replace(tzinfo=timezone.utc)
        
        print(f'\nCompleted date: {occ_dt}')
        print(f'Stored hash: {stored_hash}')
        
        # What the client would compute when marking complete
        # (It gets the occurrence from the server with current title and RRULE)
        client_hash = occurrence_hash('todo', todo_id, occ_dt, rrule, title)
        print(f'\nClient would compute hash: {client_hash}')
        print(f'Matches stored? {client_hash == stored_hash}')
        
        # Try with empty/None rrule
        hash_no_rrule = occurrence_hash('todo', todo_id, occ_dt, None, title)
        print(f'\nHash with NO rrule: {hash_no_rrule}')
        print(f'Matches stored? {hash_no_rrule == stored_hash}')
        
        hash_empty_rrule = occurrence_hash('todo', todo_id, occ_dt, '', title)
        print(f'\nHash with EMPTY rrule: {hash_empty_rrule}')
        print(f'Matches stored? {hash_empty_rrule == stored_hash}')
    
    conn.close()

if __name__ == '__main__':
    main()
