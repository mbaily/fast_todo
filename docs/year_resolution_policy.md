Year-resolution policy for parsed month/day dates

Decision summary

- Window behavior: Option A — when a calendar query window spans multiple years, return all candidate occurrences for the parsed month/day that fall inside the window.
- Year resolution (no calendar window): choose the single candidate year that yields the next occurrence >= todo.created_at (i.e., the first upcoming occurrence relative to the todo creation time).
- Feb 29 handling: prefer the next real Feb 29 (next leap year). No fallback to Feb 28 unless explicitly requested.

Resolution algorithm (high level)

1. Extraction phase (no change to natural-language parsing): parse the input text and detect whether the input contained an explicit year. If the input has a year, use it directly.

2. Yearless detection: if the parser matched only month/day (or otherwise the textual input had no explicit year token), mark the result as "yearless" and return the month/day token plus any time-of-day information, without fabricating a year.

3. Occurrence expansion (calendar endpoint or caller with window):
   - If a query window (start, end) is provided, generate candidate datetimes for each year in the inclusive range [start.year, end.year]. For each year Y create candidate date for month/day in Y and include it if it lies within [start, end]. Return all such candidates (Option A). If time-of-day present, preserve it; otherwise treat as midnight UTC.
   - If no window or the window produced no matches, resolve a single year by picking the earliest candidate datetime >= todo.created_at (the todo creation timestamp). If none within the next 12 months, pick the nearest future candidate (this is primarily to handle Feb 29 edge cases where next occurrence may be >1 year away).

4. Feb 29: when month/day is (2,29), consider candidate years that are leap years only. If resolving by creation-time, find the next leap year >= todo.created_at.year (or the next leap year that makes the candidate >= created_at). When resolving inside a window, include only leap-year candidates that fall inside the window.

5. Timezones and normalization: treat parsed datetimes as timezone-aware in UTC when performing comparisons. If time-of-day isn't present, use 00:00:00 UTC for comparison.

Acceptance tests (minimal)

- Case A (window): todo created Dec 2025 with text "Jan 22"; calendar query window covers Jan 2026 -> occurrence should include 2026-01-22. If window also covers Jan 2027, both 2026-01-22 and 2027-01-22 should be returned.

- Case B (creation-time resolution): todo created 2025-06-01 with text "Aug 23" and no query window -> occurrence should be 2025-08-23 (first upcoming occurrence >= creation time).

- Case C (no window, crossing-year creation): todo created 2025-12-15 with text "Jan 22" and no window -> occurrence should be 2026-01-22.

- Case D (Feb 29): todo created 2025-01-01 with text "Feb 29" and no window -> occurrence should be 2028-02-29 (next leap year).

Implementation notes

- Prefer returning a small structured result from `extract_dates()` indicating whether the year was explicit or not (e.g., return objects or tuples like (dt_or_monthday, year_explicit: bool)). Avoid silently fabricating the current year inside `extract_dates()`; leave year resolution to the caller (calendar endpoints) that have access to a window and the todo's creation time.
- Alternatively, add an optional API to `extract_dates(text, resolve=False, reference_dt=None, window=None)` that performs resolution when requested; keep the default behavior to only parse and mark yearless matches.

Follow-ups

- Implementation: update `app/utils.py` to mark yearless parses and `app/main.py` calendar endpoints to expand yearless matches using the algorithm above.
- Add unit tests in `tests/` for the acceptance cases.

Recorded on: 2025-08-25
