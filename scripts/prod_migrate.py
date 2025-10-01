#!/usr/bin/env python3
"""
Lightweight production migration script.
Makes occ_hash nullable in one simple transaction.

Usage:
  ./scripts/prod_migrate.py                    # Uses ./fast_todo.db
  ./scripts/prod_migrate.py /path/to/db.db     # Custom path
"""
import sys
import sqlite3
from datetime import datetime

def migrate(db_path='fast_todo.db'):
    """Migrate database to make occ_hash nullable."""
    print(f"🔄 Migrating {db_path}...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if already migrated
        cursor.execute("PRAGMA table_info(completedoccurrence)")
        for col in cursor.fetchall():
            if col[1] == 'occ_hash' and col[3] == 0:  # notnull = 0 means nullable
                print("✅ Already migrated - occ_hash is nullable")
                return True
        
        print("📝 Starting migration...")
        cursor.execute("BEGIN TRANSACTION")
        
        # Create new table with nullable occ_hash
        cursor.execute("""
            CREATE TABLE completedoccurrence_new (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                occ_hash VARCHAR,
                item_type VARCHAR,
                item_id INTEGER,
                occurrence_dt DATETIME,
                completed_at DATETIME,
                metadata_json TEXT,
                FOREIGN KEY(user_id) REFERENCES user(id)
            )
        """)
        
        # Copy all data
        cursor.execute("""
            INSERT INTO completedoccurrence_new 
            SELECT * FROM completedoccurrence
        """)
        rows = cursor.rowcount
        
        # Swap tables
        cursor.execute("DROP TABLE completedoccurrence")
        cursor.execute("ALTER TABLE completedoccurrence_new RENAME TO completedoccurrence")
        
        # Recreate indexes
        cursor.execute("CREATE INDEX ix_completedoccurrence_user_id ON completedoccurrence(user_id)")
        cursor.execute("CREATE INDEX ix_completedoccurrence_occ_hash ON completedoccurrence(occ_hash)")
        
        conn.commit()
        print(f"✅ Migration complete! ({rows} rows migrated)")
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Migration failed: {e}")
        return False
    finally:
        conn.close()

if __name__ == '__main__':
    db = sys.argv[1] if len(sys.argv) > 1 else 'fast_todo.db'
    
    # Create automatic backup
    import shutil
    backup = f"{db}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        shutil.copy2(db, backup)
        print(f"💾 Backup created: {backup}")
    except Exception as e:
        print(f"⚠️  Could not create backup: {e}")
        response = input("Continue without backup? (yes/no): ")
        if response.lower() != 'yes':
            sys.exit(1)
    
    success = migrate(db)
    sys.exit(0 if success else 1)
