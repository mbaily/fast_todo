"""Simple runtime configuration for the Fast Todo app.

Control flags are read from environment variables to allow toggling in
development or production without code changes.
"""
import os


def _trueish(v: str | None) -> bool:
    if not v:
        return False
    return v.lower() in ('1', 'true', 'yes', 'on')


# When False, server API endpoints that expand recurring events will
# return empty occurrence lists (useful for testing client behavior).
# ENABLE_RECURRING_DETECTION = _trueish(os.getenv('ENABLE_RECURRING_DETECTION', '1'))
# For local debugging and tests we previously defaulted to disabling recurrence
# detection so clients that only want explicit dates would get them by default.
# Re-enable recurrence detection by default so the server expands inline
# recurrence phrases when parsing note text. Use the environment variable
# ENABLE_RECURRING_DETECTION=0 to disable again if needed.
ENABLE_RECURRING_DETECTION = True

# Date ordering preference: 'DMY' (day-month-year) or 'MDY' (month-day-year).
# Read from environment variable DATE_ORDER; default to 'DMY' for Australian-style
# parsing. Accept lowercase variants.
DATE_ORDER = os.getenv('DATE_ORDER', 'DMY').upper()
