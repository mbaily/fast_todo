# Removing occ_hash: Better Database Design for CompletedOccurrence

## Current State Analysis

### What is `occ_hash`?

`occ_hash` is a SHA256 hash computed from:
```python
{
  'type': 'todo',
  'id': 123,
  'dt': '2025-10-01T00:00:00Z',
  'rrule': 'FREQ=WEEKLY',
  'title': 'Doctor appointment'
}
```

It was designed as a **client-server agreed identifier** for a specific occurrence instance.

### Problems with `occ_hash`

1. **Fragile**: Changes when title or rrule change
2. **Redundant**: The hash includes data already in the database
3. **Not a true key**: Doesn't enforce uniqueness properly
4. **Overhead**: Requires SHA256 computation on every occurrence
5. **Already bypassed**: The codebase has a metadata fallback that proves it's unnecessary

### Current Usage

The hash is used in TWO ways:

1. **Completion lookup** (main use):
   ```python
   done_set = set(r.occ_hash for r in done_rows)
   if o.get('occ_hash') in done_set:
       o['completed'] = True
   ```

2. **Idempotency check** (secondary):
   ```python
   exists_q = await sess.scalars(
       select(CompletedOccurrence)
       .where(CompletedOccurrence.user_id == user_id)
       .where(CompletedOccurrence.occ_hash == hash)
   )
   ```

**BUT**: The code already has a metadata fallback that works better:
```python
meta_done = set((item_type, item_id, occurrence_dt_utc) for ...)
if (type, id, dt) in meta_done:
    o['completed'] = True  # Works even when hash breaks!
```

## Proposed Solution: Use Natural Key

### What Fields to Use

The natural composite key for a completion is:

```python
(user_id, item_type, item_id, occurrence_dt)
```

These four fields **uniquely identify** a completion and are:
- ✅ Stable (never change)
- ✅ Meaningful (actual database relationships)
- ✅ Efficient (can be indexed)
- ✅ Logical (match the domain model)

### Database Schema Changes

#### Option 1: Add Composite Unique Index (Simplest)

```python
class CompletedOccurrence(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='user.id')
    item_type: str  # Make non-nullable
    item_id: int    # Make non-nullable
    occurrence_dt: datetime  # Make non-nullable
    completed_at: datetime = Field(default_factory=now_utc)
    metadata_json: Optional[str] = None
    
    # DEPRECATED: Keep for migration, but stop using
    occ_hash: Optional[str] = Field(default=None, index=False)
    
    __table_args__ = (
        Index('idx_completion_unique', 
              'user_id', 'item_type', 'item_id', 'occurrence_dt', 
              unique=True),
    )
```

**Migration SQL**:
```sql
-- Make fields non-nullable (after backfilling any NULLs)
ALTER TABLE completedoccurrence 
  ALTER COLUMN item_type SET NOT NULL,
  ALTER COLUMN item_id SET NOT NULL,
  ALTER COLUMN occurrence_dt SET NOT NULL;

-- Add unique index
CREATE UNIQUE INDEX idx_completion_unique 
ON completedoccurrence(user_id, item_type, item_id, occurrence_dt);

-- Optional: Drop old index on occ_hash
DROP INDEX IF EXISTS ix_completedoccurrence_occ_hash;

-- Optional: Make occ_hash nullable (for migration)
ALTER TABLE completedoccurrence ALTER COLUMN occ_hash DROP NOT NULL;
```

#### Option 2: Separate Tables (More Correct)

For proper foreign key relationships:

```python
class CompletedTodoOccurrence(SQLModel, table=True):
    """Completed todo occurrences - proper FK to todo table"""
    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    todo_id: int = Field(foreign_key='todo.id', index=True)
    occurrence_dt: datetime
    completed_at: datetime = Field(default_factory=now_utc)
    metadata_json: Optional[str] = None
    
    __table_args__ = (
        Index('idx_todo_completion', 
              'user_id', 'todo_id', 'occurrence_dt', 
              unique=True),
    )

class CompletedListOccurrence(SQLModel, table=True):
    """Completed list occurrences - proper FK to liststate table"""
    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='user.id', index=True)
    list_id: int = Field(foreign_key='liststate.id', index=True)
    occurrence_dt: datetime
    completed_at: datetime = Field(default_factory=now_utc)
    metadata_json: Optional[str] = None
    
    __table_args__ = (
        Index('idx_list_completion', 
              'user_id', 'list_id', 'occurrence_dt', 
              unique=True),
    )
```

**Benefits**:
- ✅ Proper foreign keys with CASCADE
- ✅ Type safety at database level
- ✅ Better query performance
- ✅ Cleaner domain model

**Tradeoffs**:
- More tables to maintain
- More complex migration
- More code changes needed

## Migration Strategy

### Phase 1: Stop Using Hash (Safest First Step)

1. **Update `/occurrence/complete` endpoint**:

```python
@app.post('/occurrence/complete')
async def mark_occurrence_completed(...):
    # OLD: Check by hash
    # exists_q = await sess.scalars(
    #     select(CompletedOccurrence)
    #     .where(CompletedOccurrence.user_id == current_user.id)
    #     .where(CompletedOccurrence.occ_hash == hash)
    # )
    
    # NEW: Check by metadata
    exists_q = await sess.scalars(
        select(CompletedOccurrence)
        .where(CompletedOccurrence.user_id == current_user.id)
        .where(CompletedOccurrence.item_type == item_type)
        .where(CompletedOccurrence.item_id == item_id)
        .where(CompletedOccurrence.occurrence_dt == parsed_occ_dt)
    )
    if exists_q.first():
        return {'ok': True, 'created': False}
    
    # Store without computing hash
    row = CompletedOccurrence(
        user_id=current_user.id,
        occ_hash=None,  # Stop storing hash
        item_type=item_type,
        item_id=item_id,
        occurrence_dt=parsed_occ_dt,
        metadata_json=meta_json
    )
```

2. **Update `/calendar/occurrences` endpoint**:

```python
# OLD: Build hash set
# done_set = set(r.occ_hash for r in done_rows)

# NEW: Build metadata set only
meta_done = set()
for r in done_rows:
    if r.item_type and r.item_id is not None and r.occurrence_dt:
        dt_utc = r.occurrence_dt
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        dt_utc = dt_utc.astimezone(timezone.utc)
        meta_done.add((str(r.item_type), int(r.item_id), dt_utc))

# OLD: Check both hash and metadata
# completed_by_hash = (o.get('occ_hash') in done_set)
# if not completed_by_hash:
#     ... metadata fallback ...

# NEW: Just use metadata
occ_dt = _parse_iso_z(o.get('occurrence_dt'))
key = (str(o.get('item_type')), int(o.get('id')), occ_dt)
o['completed'] = (key in meta_done)
```

3. **Update `/occurrence/uncomplete` endpoint**:

```python
# OLD: Delete by hash
# await sess.exec(delete(CompletedOccurrence)
#                .where(CompletedOccurrence.user_id == current_user.id)
#                .where(CompletedOccurrence.occ_hash == hash))

# NEW: Parse metadata from client and delete by that
# Client needs to send: item_type, item_id, occurrence_dt
await sess.exec(delete(CompletedOccurrence)
               .where(CompletedOccurrence.user_id == current_user.id)
               .where(CompletedOccurrence.item_type == item_type)
               .where(CompletedOccurrence.item_id == item_id)
               .where(CompletedOccurrence.occurrence_dt == occurrence_dt))
```

4. **Update client (HTML/JS)**:

The client already sends the metadata! Just stop requiring the hash:

```javascript
// Already sends these (from data attributes):
const itemType = cb.dataset.itemType || '';
const itemId = cb.dataset.itemId || '';
const occurrenceDt = cb.dataset.occDt || '';

// Can make hash optional
const body = `_csrf=${csrf}&item_type=${itemType}&item_id=${itemId}&occurrence_dt=${occurrenceDt}`;
// &hash=${hash}  <- Make this optional or remove entirely
```

### Phase 2: Remove Hash from Server Generation

Stop computing `occ_hash` in `add_occ()` function:

```python
def add_occ(...):
    # Remove this line:
    # occ_hash = occurrence_hash(item_type, item_id, occ_dt, rrule_str or '', title)
    
    occ_record = {
        'occurrence_dt': occ_dt.isoformat(),
        'item_type': item_type,
        'id': item_id,
        # 'occ_hash': occ_hash,  # Remove
        # ... rest of fields
    }
```

### Phase 3: Database Cleanup

After confirming Phase 1-2 work:

```sql
-- Optional: Drop the column
ALTER TABLE completedoccurrence DROP COLUMN occ_hash;
```

## Benefits of Removing `occ_hash`

1. **Simpler code**: No hash computation needed
2. **More robust**: Completions survive title/rrule changes naturally
3. **Better performance**: No SHA256 computation on every occurrence
4. **Cleaner API**: Client doesn't need to understand hashing
5. **True database relationships**: Use real FKs and constraints
6. **Easier debugging**: Can query by meaningful fields
7. **Less storage**: No 64-char hash string per completion

## Backward Compatibility

### During Migration

- Keep `occ_hash` column but make it nullable
- Accept hash from old clients but don't require it
- New code uses metadata, old completions still work via metadata fallback

### Client Compatibility

Old clients sending hash:
```
POST /occurrence/complete
hash=occ:abc123...&item_type=todo&item_id=123&occurrence_dt=2025-10-01...
```

Server: "I'll use the metadata fields, ignore the hash"

New clients:
```
POST /occurrence/complete
item_type=todo&item_id=123&occurrence_dt=2025-10-01...
```

Server: "Perfect, that's all I need!"

## Testing Strategy

1. **Unit tests**: Test completion lookup by metadata
2. **Integration tests**: Complete → reload → verify still completed
3. **E2E tests**: Complete via UI → verify in different month view
4. **Load tests**: Verify performance doesn't degrade
5. **Migration test**: Old completions with hash → new code → still work

## Rollout Plan

1. Deploy Phase 1 code (stop using hash, keep storing NULL)
2. Monitor for 1 week
3. If no issues, deploy Phase 2 (stop generating hash in occurrences)
4. Monitor for 1 week
5. If no issues, run database migration (drop column)
6. Celebrate simpler, more robust code! 🎉

## Summary

**The `occ_hash` is a vestigial organ**. It was useful before metadata fields existed, but now:
- The metadata `(item_type, item_id, occurrence_dt)` uniquely identifies completions
- The metadata is stable and survives title/rrule changes
- The codebase already has a working metadata fallback
- The hash is redundant and causes problems

**Recommendation**: Follow the migration strategy to remove `occ_hash` usage and eventually drop the column. Use natural database relationships instead.
