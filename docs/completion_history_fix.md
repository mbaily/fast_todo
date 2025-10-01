# Completion History Preservation Fix

## Problem

When a user marks calendar occurrences as complete and then later edits the underlying todo's text, the completion records become "orphaned" and the occurrences show as incomplete again.

This happens because:
1. The `occurrence_hash` function includes the todo's **title** (lowercased) in the hash calculation
2. When the title changes, the hash changes
3. The system can no longer match the old completion records to the new hash

## Solution

We now store additional metadata alongside the hash when marking occurrences complete, and use this metadata as a fallback lookup when the hash doesn't match.

### Changes Made

#### 1. Backend: `/occurrence/complete` endpoint (app/main.py ~line 4689)

**Before:**
```python
row = CompletedOccurrence(user_id=current_user.id, occ_hash=hash)
```

**After:**
```python
# Parse metadata from request
item_type_val = form_data.get('item_type')
item_id_val = int(form_data.get('item_id'))
occurrence_dt_val = datetime.fromisoformat(form_data.get('occurrence_dt'))

row = CompletedOccurrence(
    user_id=current_user.id,
    occ_hash=hash,
    item_type=item_type_val,
    item_id=item_id_val,
    occurrence_dt=occurrence_dt_val
)
```

Now we store the structured metadata (item_type, item_id, occurrence_dt) in addition to the hash.

#### 2. Backend: `/calendar/occurrences` endpoint (app/main.py ~line 4344)

**Added metadata lookup set:**
```python
done_set = set(r.occ_hash for r in done_rows)

# Build metadata lookup set for fallback
# Uses date-only (YYYY-MM-DD) since time varies between completion and occurrence
done_metadata = set()
for r in done_rows:
    if r.item_type and r.item_id is not None and r.occurrence_dt:
        date_str = r.occurrence_dt.date().isoformat()  # Date only
        done_metadata.add((str(r.item_type), str(r.item_id), date_str))
```

**Updated completion check:**
```python
# Check both hash (fast path) and metadata (fallback)
completed_by_hash = (o.get('occ_hash') in done_set)
completed_by_metadata = False

if not completed_by_hash:
    # Build metadata tuple with date-only comparison
    # (occurrences from text are at midnight, but completions at any time)
    date_str = occurrence_dt.date().isoformat()
    metadata_tuple = (item_type, item_id, date_str)
    completed_by_metadata = (metadata_tuple in done_metadata)

o['completed'] = (completed_by_hash or completed_by_metadata)
```

**Key insight**: We use date-only comparison (YYYY-MM-DD) because:
- Occurrences extracted from text like "October 20" are at midnight (00:00:00)
- Completions are stored with the actual completion timestamp (e.g., 14:30:00)
- Comparing full timestamps would fail, so we normalize to date-only

#### 3. Backend: Index context (app/main.py ~line 9594)

Applied the same dual-check logic to the `_occ_allowed` function used in the index page calendar widget.

#### 4. Frontend: Calendar page (html_no_js/templates/calendar.html)

**Added data attributes to checkboxes:**
```html
<input type="checkbox" class="occ-complete" 
    data-hash="{{ ev.occ_hash }}" 
    data-item-type="{{ ev.item_type }}" 
    data-item-id="{{ ev.id }}" 
    data-occurrence-dt="{{ ev.occurrence_dt }}"
    ...>
```

**Updated JavaScript to send metadata:**
```javascript
const itemType = cb.dataset.itemType || '';
const itemId = cb.dataset.itemId || '';
const occurrenceDt = cb.dataset.occurrenceDt || '';

const body = `_csrf=${csrf}&hash=${hash}&item_type=${itemType}&item_id=${itemId}&occurrence_dt=${occurrenceDt}`;
```

## Benefits

1. **Preserves history**: Completion records survive todo text changes
2. **Backward compatible**: Old completion records (without metadata) still work via hash
3. **Forward compatible**: New completion records have metadata for fallback
4. **Minimal overhead**: Metadata is only checked when hash doesn't match

## Existing Data

You have **42 existing completion records** that lack metadata. These records:
- Will continue to work as long as the todo text doesn't change
- Will become orphaned if the todo text changes (same as before)
- Cannot be automatically backfilled (we can't reverse SHA256 hashes)

**Recommendation**: After this fix is deployed, any new completions will be protected. For existing completions, you can:
1. Accept that old completions may be orphaned if titles change (current behavior)
2. Manually re-complete occurrences after editing todo text
3. Keep important completion notes in a separate log/journal

## Testing

Run the test to verify the fix works:
```bash
cd /home/mbaily/other_git/fast_todo
source .venv/bin/activate
PYTHONPATH=. python tools/test_completion_fix.py
```

Expected output:
```
✅ TEST PASSED

The fix is working! Completions now:
1. Store metadata (item_type, item_id, occurrence_dt)
2. Can be found by metadata even when title changes
3. Your completion history is preserved!
```

## Database Schema

No migration needed! The `CompletedOccurrence` table already has these optional fields:
- `item_type` (string, optional)
- `item_id` (integer, optional)  
- `occurrence_dt` (datetime, optional)

They were previously unused (always NULL), and now they're being populated.

## Performance

- Hash lookup: O(1) - same as before
- Metadata fallback: O(1) - uses a set for fast lookup
- Minimal memory overhead: ~50 bytes per completion record
- No additional database queries

## Future Improvements

Consider removing `title` from the hash calculation entirely in a future major version:
- Pro: Simpler, no fallback needed
- Con: Breaking change, all existing completions orphaned
- Migration: Would need to recreate all completion records with new hashes
