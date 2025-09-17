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

# Default server timezone name (IANA). Set to Melbourne/Australia by default.
# Can be overridden via environment or app/local_config.py. Used by date
# formatting and any synthesized datetimes that should reflect local walltime.
DEFAULT_TIMEZONE = os.getenv('DEFAULT_TIMEZONE', 'Australia/Melbourne')

# Enable lightweight assertion logging to help debug calendar/index mismatches.
# When True, assertion messages will be appended to `scripts/index_calendar.log`.
# Set via environment variable ENABLE_INDEX_CALENDAR_ASSERTS=1 to enable.
#ENABLE_INDEX_CALENDAR_ASSERTS = _trueish(os.getenv('ENABLE_INDEX_CALENDAR_ASSERTS', '0'))

# Number of days before/after now to include in the small index calendar
# (default 1 keeps previous behavior). Increasing this will let near-future
# explicit dates (like '12/9/25') show up on the index page without changing
# the broader /calendar/occurrences API window.
try:
    INDEX_CALENDAR_DAYS = int(os.getenv('INDEX_CALENDAR_DAYS', '1'))
except Exception:
    INDEX_CALENDAR_DAYS = 1
INDEX_CALENDAR_DAYS = 2


# When true, disable text scanning for dates/recurrence in calendar computations.
# This affects the /calendar/occurrences API and the small calendar block on index.html.
# Persisted recurrence fields (recurrence_rrule/recurrence_dtstart) and deferred_until
# are still honored. Set via environment variable DISABLE_CALENDAR_TEXT_SCAN=1
DISABLE_CALENDAR_TEXT_SCAN = _trueish(os.getenv('DISABLE_CALENDAR_TEXT_SCAN', '0'))


DOKUWIKI_NOTE_LINK_PREFIX = os.getenv('DOKUWIKI_NOTE_LINK_PREFIX', 'https://myserver.hopto.org/dokuwiki/doku.php?id=')

# Optional local overrides: define variables in app/local_config.py to extend or
# override the defaults above without changing versioned config.
# Don't put your server for dokuwiki in version control in this project's git
# So don't add app/local_config.py to the git repository.
try:
    from .local_config import *  # type: ignore  # noqa: F401,F403
except ImportError:
    # No local overrides present; proceed with defaults.
    pass

