Logic layering (phase 1)
--------------------------------------------------

- app/main.py: FastAPI routes, request/response wiring, and async DB access.
- app/app_logic.py: synchronous business logic functions. No routes, no async.
- app/app_logic_json.py: JSON-only adapter around app_logic. Accepts/returns
  plain JSON structures.

Current functions moved:
- Serialization helpers: serialize_todo, serialize_list
- Secondary priority calculation for lists: compute_secondary_priority_for_list

Next candidates to move:
- Completion state helpers and toggle logic
- Hashtag parsing/sync helpers
- Category sort/move calculations

Guidelines:
- New logic should prefer app_logic (sync, plain Python). Routes in main.py call
  these functions.
- app_logic_json is for future routes that speak JSON-only.
