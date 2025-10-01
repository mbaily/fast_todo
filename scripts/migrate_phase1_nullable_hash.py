#!/usr/bin/env python3
"""
Phase 1 Migration: Make occ_hash nullable in CompletedOccurrence table.

This allows new completions to be stored without a hash, using only metadata
(item_type, item_id, occurrence_dt) as the natural key.

IMPORTANT: This modifies the database schema. Backup first!

Usage:
  python scripts/migrate_phase1_nullable_hash.py --db fast_todo.db --dry-run
  python scripts/migrate_phase1_nullable_hash.py --db fast_todo.db --commit
"""
import argparse
import sqlite3
import sys

def migrate_nullable_hash(db_path: str, dry_run: bool = True):
    """Make occ_hash nullable in completedoccurrence table."""
    
    print(f"Phase 1 Migration: Make occ_hash nullable")
    print(f"Database: {db_path}")
    print(f"Mode: {'DRY RUN' if dry_run else 'COMMIT'}\n")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check current schema
        cursor.execute("PRAGMA table_info(completedoccurrence)")
        columns = cursor.fetchall()
        
        print("Current schema:")
        for col in columns:
            col_id, name, col_type, notnull, default, pk = col
            nullable = "NULL" if notnull == 0 else "NOT NULL"
            print(f"  {name}: {col_type} {nullable}")
        
        # Find occ_hash column
        occ_hash_col = None
        for col in columns:
            if col[1] == 'occ_hash':
                occ_hash_col = col
                break
        
        if not occ_hash_col:
            print("\n❌ ERROR: occ_hash column not found!")
            return False
        
        col_id, name, col_type, notnull, default, pk = occ_hash_col
        
        if notnull == 0:
            print(f"\n✅ occ_hash is already nullable - no migration needed!")
            return True
        
        print(f"\n🔧 occ_hash is currently NOT NULL - migration needed")
        
        if dry_run:
            print("\n📝 Would execute SQL:")
            print("""
-- SQLite doesn't support ALTER COLUMN directly, must recreate table
BEGIN TRANSACTION;

-- Create new table with nullable occ_hash
CREATE TABLE completedoccurrence_new (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    occ_hash VARCHAR NULL,  -- Changed from NOT NULL to NULL
    item_type VARCHAR,
    item_id INTEGER,
    occurrence_dt DATETIME,
    completed_at DATETIME,
    metadata_json TEXT,
    FOREIGN KEY(user_id) REFERENCES user(id)
);

-- Copy all data
INSERT INTO completedoccurrence_new 
SELECT id, user_id, occ_hash, item_type, item_id, occurrence_dt, completed_at, metadata_json
FROM completedoccurrence;

-- Drop old table
DROP TABLE completedoccurrence;

-- Rename new table
ALTER TABLE completedoccurrence_new RENAME TO completedoccurrence;

-- Recreate indexes
CREATE INDEX ix_completedoccurrence_user_id ON completedoccurrence(user_id);
CREATE INDEX ix_completedoccurrence_occ_hash ON completedoccurrence(occ_hash);

COMMIT;
""")
            print("✋ Dry run complete - no changes made")
            return True
        
        # Execute migration
        print("\n🚀 Executing migration...")
        
        # Begin transaction
        cursor.execute("BEGIN TRANSACTION")
        
        # Create new table with nullable occ_hash
        cursor.execute("""
            CREATE TABLE completedoccurrence_new (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                occ_hash VARCHAR NULL,
                item_type VARCHAR,
                item_id INTEGER,
                occurrence_dt DATETIME,
                completed_at DATETIME,
                metadata_json TEXT,
                FOREIGN KEY(user_id) REFERENCES user(id)
            )
        """)
        print("  ✓ Created new table")
        
        # Copy data
        cursor.execute("""
            INSERT INTO completedoccurrence_new 
            SELECT id, user_id, occ_hash, item_type, item_id, occurrence_dt, completed_at, metadata_json
            FROM completedoccurrence
        """)
        row_count = cursor.rowcount
        print(f"  ✓ Copied {row_count} rows")
        
        # Drop old table
        cursor.execute("DROP TABLE completedoccurrence")
        print("  ✓ Dropped old table")
        
        # Rename new table
        cursor.execute("ALTER TABLE completedoccurrence_new RENAME TO completedoccurrence")
        print("  ✓ Renamed new table")
        
        # Recreate indexes
        cursor.execute("CREATE INDEX ix_completedoccurrence_user_id ON completedoccurrence(user_id)")
        cursor.execute("CREATE INDEX ix_completedoccurrence_occ_hash ON completedoccurrence(occ_hash)")
        print("  ✓ Recreated indexes")
        
        # Commit
        conn.commit()
        print("\n✅ Migration completed successfully!")
        
        # Verify
        cursor.execute("PRAGMA table_info(completedoccurrence)")
        new_columns = cursor.fetchall()
        print("\nNew schema:")
        for col in new_columns:
            col_id, name, col_type, notnull, default, pk = col
            nullable = "NULL" if notnull == 0 else "NOT NULL"
            print(f"  {name}: {col_type} {nullable}")
        
        return True
        
    except Exception as e:
        print(f"\n❌ ERROR during migration: {e}")
        if not dry_run:
            conn.rollback()
            print("  Rolled back transaction")
        return False
        
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description='Phase 1 Migration: Make occ_hash nullable')
    parser.add_argument('--db', required=True, help='Path to SQLite database file')
    parser.add_argument('--commit', action='store_true', help='Actually perform migration (default is dry-run)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    
    args = parser.parse_args()
    
    # Default to dry-run unless --commit is specified
    dry_run = not args.commit or args.dry_run
    
    if not dry_run:
        print("⚠️  WARNING: This will modify your database!")
        response = input("Are you sure you want to continue? (yes/no): ")
        if response.lower() != 'yes':
            print("Migration cancelled")
            sys.exit(0)
    
    success = migrate_nullable_hash(args.db, dry_run=dry_run)
    
    if not success:
        sys.exit(1)

if __name__ == '__main__':
    main()
