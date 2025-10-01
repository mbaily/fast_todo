# Phantom Occurrence Date Window Filter Fix

## Issue Summary

**Problem**: When marking todos complete in month 10 (October), phantom occurrences for those completions were appearing in month 9 (September) calendar view.

**Affected Todos**: 549, 392, 397 (all marked complete in October 2025)

## Root Cause

The `inject_phantom_occurrences()` function in `app/utils.py` was fetching **ALL** completed occurrences for the user, regardless of the date window being viewed. This caused:

1. User views September calendar (Sept 1-30)
2. Server generates occurrences for September dates only
3. Phantom injection logic fetches all user completions (including October)
4. October completions don't match any September occurrences (correct!)
5. Phantom logic incorrectly injects October completions as "orphaned" phantoms into September

## Why These Todos Were Affected

These todos had a subtle hash mismatch issue:
- The stored completion hashes were computed with a different RRULE than the current one
- This prevented the hash-based deduplication from working
- The metadata-based fallback (item_id + occurrence_dt) correctly matched the completion
- But the phantom logic saw the completion as "not in existing_keys" and created a phantom
- **The phantom had an October date but was showing in September's calendar!**

## Solution

Added date window filtering to `inject_phantom_occurrences()`:

### Changes Made

#### 1. Updated function signature (`app/utils.py`)
```python
async def inject_phantom_occurrences(owner_id: int, occurrences: list, sess=None, start_dt=None, end_dt=None):
```

Added optional `start_dt` and `end_dt` parameters to filter completions by date window.

#### 2. Added date filtering logic (`app/utils.py`)
```python
# Filter by date window if provided to avoid injecting phantoms
# outside the viewing window (e.g., don't inject October completions
# when viewing September)
if start_dt is not None and d < start_dt:
    continue
if end_dt is not None and d > end_dt:
    continue
```

#### 3. Updated caller (`app/main.py`)
```python
await inject_phantom_occurrences(owner_id, occurrences, sess, start_dt=start_dt, end_dt=end_dt)
```

Now passes the date window from the calendar request.

## Test Results

Before fix (viewing September):
```
WITHOUT date filter:
  Phantoms for TODO 549: 1 (date: 2025-10-01)  ← WRONG!
  Phantoms for TODO 392: 1 (date: 2025-10-02)  ← WRONG!
  Phantoms for TODO 397: 1 (date: 2025-10-03)  ← WRONG!
```

After fix (viewing September):
```
WITH date filter (September window):
  Phantoms for TODO 549: 0  ✓ CORRECT
  Phantoms for TODO 392: 0  ✓ CORRECT
  Phantoms for TODO 397: 0  ✓ CORRECT
```

After fix (viewing October):
```
WITH date filter (October window):
  Phantoms for TODO 549: 1 (date: 2025-10-01)  ✓ CORRECT
  Phantoms for TODO 392: 1 (date: 2025-10-02)  ✓ CORRECT
  Phantoms for TODO 397: 1 (date: 2025-10-03)  ✓ CORRECT
```

## Backwards Compatibility

The `start_dt` and `end_dt` parameters are optional and default to `None`. When `None`:
- No filtering is applied (old behavior)
- All completed occurrences are considered for phantom injection
- This is appropriate for contexts where date window filtering isn't needed (e.g., tests)

Existing test `tests/test_phantom_inject.py` passes without modification.

## Impact

This fix ensures:
1. ✅ Phantom occurrences only appear in the month they were completed
2. ✅ No "leaked" completions from future/past months in calendar view
3. ✅ Completion history preservation still works within the correct date window
4. ✅ Hash mismatches (RRULE changes) are handled correctly via metadata fallback
5. ✅ Deduplication by hash still works to prevent true duplicates

## Files Modified

- `app/utils.py`: Updated `inject_phantom_occurrences()` signature and logic
- `app/main.py`: Updated call to pass date window parameters
- `scripts/test_phantom_fix.py`: Test script to verify the fix (new)

## Related Documentation

- `docs/phantom_occurrences.md`: Original phantom occurrence feature documentation
- `docs/completion_history_complete.md`: Completion history preservation feature
