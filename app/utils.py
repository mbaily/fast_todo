from datetime import datetime, timezone
import logging
import re
from typing import List, Tuple, Optional
try:
    import dateparser.search
    import dateparser
except Exception:
    dateparser = None
    dateparser_search = None
else:
    dateparser_search = dateparser.search
    # Prefer DateDataParser when available; seed with English to avoid full
    # automatic language detection overhead.
    try:
        from dateparser.date import DateDataParser
        _DATE_DATA_PARSER = DateDataParser(languages=['en'])
    except Exception:
        _DATE_DATA_PARSER = None

logger = logging.getLogger(__name__)

# English month names used for lightweight span detection in parse_date_and_recurrence
MONTHS_EN = [
    'January','February','March','April','May','June','July','August','September','October','November','December'
]

# Common English number-words that can be mistaken for months/days when
# parsed in isolation by DateDataParser (e.g., 'eight' -> August). We treat
# a single-token match against these as non-date to avoid false-positives.
NUMBER_WORDS = {
    'zero','one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve'
}

# Generic single-token anchors that should not by themselves or as part of a
# matched span produce a date (e.g. 'today', 'now', 'tonight'). Treat any
# matched span that contains one of these tokens as non-date to avoid
# spurious detections like '25th today'.
GENERIC_ANCHOR_BLACKLIST = {'now', 'today', 'tonight', 'tonite', 'this', 'next'}


def _contains_generic_anchor(s: str) -> bool:
    """Return True if any generic anchor token appears as a word in s."""
    if not s:
        return False
    sl = s.lower()
    for w in GENERIC_ANCHOR_BLACKLIST:
        if re.search(r"\b" + re.escape(w) + r"\b", sl):
            return True
    return False


def _contains_time_token(s: str) -> bool:
    """Return True if s contains a time-like token (e.g., '9am', '09:00', 'at 9', 'at 09:00am')."""
    if not s:
        return False
    sl = s.lower()
    # common time patterns: 9am, 9 pm, 09:00, 9:00am, at 9, at 09:00
    if re.search(r"\b(at\s+)?\d{1,2}(:\d{2})?\s*(am|pm)?\b", sl):
        return True
    # standalone 24-hour times like 17:30
    if re.search(r"\b\d{1,2}:\d{2}\b", sl):
        return True
    # phrases like 'in the morning', 'in the evening', 'tonight' (though 'tonight' is blacklisted elsewhere)
    if re.search(r"\b(in the (morning|evening|afternoon)|morning|evening|afternoon|noon|midnight)\b", sl):
        return True
    return False


def _contains_date_anchor(s: str) -> bool:
    """Return True if s contains an explicit date anchor (month name, weekday, or numeric date)."""
    if not s:
        return False
    sl = s.lower()
    # month names
    for m in MONTHS_EN:
        if re.search(r"\b" + re.escape(m.lower()) + r"\b", sl):
            return True
        if re.search(r"\b" + re.escape(m[:3].lower()) + r"\b", sl):
            return True
    # weekday names or 'on monday' style
    if re.search(r"\b(on\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b", sl):
        return True
    if re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", sl):
        return True
    # numeric day/month like '5/9' or '5-9' or '5th'
    if re.search(r"\b\d{1,2}[./-]\d{1,2}\b", sl) or re.search(r"\b\d{1,2}(st|nd|rd|th)\b", sl):
        return True
    return False


def _has_time_or_date_anchor(s: str) -> bool:
    """Return True if s contains a time token or explicit date anchor."""
    return _contains_time_token(s) or _contains_date_anchor(s)


def now_utc() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


def normalize_hashtag(tag: str) -> str:
    """Normalize a hashtag: strip whitespace, ensure it starts with '#'.

    This preserves the tag text casing after the '#'.
    """
    if tag is None:
        return tag
    t = tag.strip()
    if not t:
        # empty or whitespace-only tags are invalid
        raise ValueError("invalid hashtag: empty")
    if not t.startswith('#'):
        t = '#' + t
    # validate: must start with a letter and only contain alphanumeric characters
    body = t[1:]
    # empty body is invalid
    if not body:
        raise ValueError("invalid hashtag: empty")
    # first char must be a letter
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", body):
        raise ValueError("invalid hashtag: must start with a letter and be alphanumeric after '#'")
    # enforce lowercase body
    return '#' + body.lower()


def extract_hashtags(text: str | None) -> list[str]:
    """Extract candidate hashtags from arbitrary text and return a list of
    normalized hashtag strings (e.g. '#work'). Only returns valid, normalized
    tags; invalid-looking candidates are ignored.
    """
    if not text:
        return []
    # find whole-token sequences starting with '#' followed by a letter then zero or more alphanumeric chars
    # require start-of-string or whitespace before the '#' and whitespace or end after the token
    matches = []
    for m in re.finditer(r"(?:(?<=\s)|^)#([A-Za-z][A-Za-z0-9]*)(?=\s|$)", text):
        matches.append(m.group(1))
    seen: set[str] = set()
    out: list[str] = []
    for m in matches:
        candidate = f"#{m}"
        try:
            n = normalize_hashtag(candidate)
        except ValueError:
            continue
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def remove_hashtags_from_text(text: str | None) -> str:
    """Remove hashtag tokens (e.g. #work) from text and normalize spaces.

    - Removes standalone #alnum sequences regardless of position.
    - Collapses multiple whitespace to a single space.
    - Trims leading/trailing whitespace.
    - If input is None, return empty string.
    """
    if not text:
        return ""
    # Remove hashtags that match the stricter rule (start with letter, then alnum)
    # with optional leading space; keep a space so words don't join
    cleaned = re.sub(r"(^|\s)#[A-Za-z][A-Za-z0-9]*(?=\s|$)", lambda m: (" " if m.group(1) else ""), text)
    # Collapse all whitespace sequences to a single space and strip
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def format_server_local(dt, fmt: str = '%Y-%m-%d %H:%M:%S') -> str:
    """Format a datetime-like value into the server's local time.

    - If dt is None return empty string.
    - If dt is a string, return it unchanged (assume already formatted).
    - If dt is a timezone-aware datetime, convert to local timezone and format.
    - If dt is naive datetime, treat it as UTC then convert to local timezone.
    """
    if dt is None:
        return ''
    # If already a string, don't attempt parsing here; templates may already
    # receive ISO-formatted strings from JSON endpoints.
    if isinstance(dt, str):
        return dt
    try:
        # handle datetime objects
        from datetime import datetime as _dt
        if isinstance(dt, _dt):
            # if naive, assume UTC
            if dt.tzinfo is None:
                aware = dt.replace(tzinfo=timezone.utc)
            else:
                aware = dt
            # convert to local timezone
            local = aware.astimezone()
            return local.strftime(fmt)
    except Exception:
        logger.exception("failed to format datetime in server local timezone")
    # fallback: str()
    return str(dt)


def format_in_timezone(dt, tz_name: str | None, fmt: str = '%Y-%m-%d %H:%M:%S') -> str:
    """Format a datetime into the named timezone. If tz_name is None, fall back
    to server localtime (format_server_local).
    """
    if not tz_name:
        return format_server_local(dt, fmt)
    try:
        import zoneinfo
        import urllib.parse
        # tolerate URL-encoded tz names (e.g. Australia%2FMelbourne)
        if isinstance(tz_name, str):
            tz_name = urllib.parse.unquote(tz_name)
        if isinstance(dt, str):
            return dt
        from datetime import datetime as _dt
        if isinstance(dt, _dt):
            if dt.tzinfo is None:
                aware = dt.replace(tzinfo=timezone.utc)
            else:
                aware = dt
            try:
                tz = zoneinfo.ZoneInfo(tz_name)
            except zoneinfo.ZoneInfoNotFoundError:
                # timezone not found; fall back to server-local formatting
                logger.exception("failed to find timezone %s", tz_name)
                return format_server_local(dt, fmt)
            return aware.astimezone(tz).strftime(fmt)
    except Exception:
        logger.exception("failed to format datetime in timezone %s", tz_name)
        return format_server_local(dt, fmt)
    return str(dt)


def extract_dates(text: str | None) -> list[datetime]:
    """Extract dates from freeform text using dateparser's search functionality.

    Returns a list of timezone-aware datetimes normalized to UTC. If dateparser
    is not installed or no dates are found, returns an empty list.
    """
    if not text:
        return []
    if dateparser is None or dateparser_search is None:
        logger.warning('dateparser not available; extract_dates will return empty list')
        return []
    try:
        # Prefer the seeded DateDataParser instance if available; it's much
        # faster because it caches or prioritizes the seeded languages.
        out: list[datetime] = []
        if _DATE_DATA_PARSER is not None:
            dd = _DATE_DATA_PARSER
            res = dd.get_date_data(text)
            # res is a DateData object with attribute 'date_obj'
            dt = getattr(res, 'date_obj', None)
            if dt:
                # If the input contains explicit numeric separators (e.g.
                # '12/9/2025' or '12-9-25'), prefer our targeted numeric
                # substring parser to interpret day/month ordering (AU) rather
                # than relying on DateDataParser which often assumes
                # month/day ordering for numeric triplets.
                if re.search(r"\d[./-]\d", text):
                    # ignore ddp result and continue to explicit parsing below
                    dt = None
                else:
                    # Guard: if the input text is a single token that is a
                    # number-word or a short numeric (e.g. '8' or 'eight'), don't
                    # treat DateDataParser's ambiguous month/day 'period' result as
                    # a real date — fall back to search_dates instead.
                    token = text.strip().lower()
                    if (token in NUMBER_WORDS) or re.fullmatch(r"\d{1,2}", token):
                        # ignore this ddp result and continue to fallback
                        dt = None
                    else:
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        out.append(dt.astimezone(timezone.utc))
                        return out
        # fallback to search_dates (English only)
        # Before calling search_dates, try a targeted explicit-substring scan
        # for common date formats (numeric dd/mm or dd-mm, and month-name
        # with day). This avoids cases where search_dates captures a larger
        # span that includes recurrence tokens (e.g. 'every 2 weeks 5/9').
        # Read date ordering preference from config (DMY or MDY). Import here
        # to avoid circular imports at module load time.
        try:
            from app import config as _config
            DATE_ORDER = getattr(_config, 'DATE_ORDER', 'DMY')
        except Exception:
            DATE_ORDER = 'DMY'

        def _explicit_date_substrings(t: str) -> list[datetime]:
            out_local: list[datetime] = []
            try:
                # numeric patterns like 5/9 or 05-09 or 2025/09/05
                num_re = re.compile(r"\b(\d{1,4})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b")
                # Default numeric triplet interpretation to day/month[/year]
                # (Australian-style). If the first token is a 4-digit year we
                # treat the pattern as YYYY/MM/DD. If a token is >12 it is
                # treated as the day to disambiguate.
                for m in num_re.finditer(t):
                    g1, g2, g3 = m.group(1), m.group(2), m.group(3)
                    try:
                        a = int(g1)
                        b = int(g2)
                    except Exception:
                        continue
                    # If an explicit year token is present, parse it and
                    # interpret the first two numeric tokens as day/month by
                    # default (Australian-style). This avoids deferring to
                    # dateparser which often interprets numeric triplets as
                    # month/day/year (US) and yields incorrect months like
                    # December for '12/9/2025'.
                    year = None
                    if g3:
                        try:
                            year = int(g3)
                            if year < 100:  # two-digit year
                                year += 2000
                        except Exception:
                            year = None
                    # If the year was parsed above, use it; otherwise we'll
                    # later synthesize a year from the current time when
                    # needed.
                    # Interpret numeric triplets: if the first token is a
                    # 4-digit year, treat as YYYY/MM/DD; otherwise treat as
                    # DD/MM/YYYY (Australian-style). If one token is >12 it
                    # will be treated as the day to disambiguate.
                    if g3:
                        # g3 is present (year-like). Decide ordering.
                        if len(g1) == 4:
                            # pattern like YYYY/MM/DD -> g1=YYYY, g2=MM, g3=DD
                            y = a
                            mon = b
                            try:
                                day = int(g3)
                            except Exception:
                                day = 1
                        else:
                            # With an explicit year present, respect the
                            # configured DATE_ORDER. If DATE_ORDER == 'MDY'
                            # default to month/day; otherwise default to
                            # day/month. In either case a token >12 forces
                            # that token to be treated as the day.
                            if a > 12 and 1 <= b <= 12 and 1 <= a <= 31:
                                day, mon = a, b
                            elif b > 12 and 1 <= a <= 12 and 1 <= b <= 31:
                                day, mon = b, a
                            else:
                                if DATE_ORDER == 'MDY':
                                    mon, day = a, b
                                else:
                                    day, mon = a, b
                            y = year or now_utc().year
                    else:
                        if a > 12 and 1 <= b <= 12 and 1 <= a <= 31:
                            day, mon = a, b
                        elif b > 12 and 1 <= a <= 12 and 1 <= b <= 31:
                            day, mon = b, a
                        else:
                            day, mon = a, b
                        y = year or now_utc().year
                    # sanity ranges
                    if 1 <= mon <= 12 and 1 <= day <= 31:
                        from datetime import datetime as _dt
                        try:
                            cand = _dt(y, mon, day, tzinfo=timezone.utc)
                            out_local.append(cand)
                        except Exception:
                            # try a few successive years if invalid (e.g., Feb29)
                            for yy in range(now_utc().year, now_utc().year + 3):
                                try:
                                    cand = _dt(yy, mon, day, tzinfo=timezone.utc)
                                    out_local.append(cand)
                                    break
                                except Exception:
                                    continue

                # month-name patterns like '10 Sep' or 'Sep 10' (with optional suffix)
                try:
                    month_map = {m[:3].lower(): i+1 for i, m in enumerate(MONTHS_EN)}
                    dm_re = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?\b", flags=re.IGNORECASE)
                    md_re = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", flags=re.IGNORECASE)
                    for m in dm_re.finditer(t):
                        day = int(m.group(1))
                        mon_key = m.group(2).lower()[:3]
                        mon = month_map.get(mon_key)
                        if mon and 1 <= day <= 31:
                            from datetime import datetime as _dt
                            y = now_utc().year
                            try:
                                out_local.append(_dt(y, mon, day, tzinfo=timezone.utc))
                            except Exception:
                                for yy in range(y, y + 3):
                                    try:
                                        out_local.append(_dt(yy, mon, day, tzinfo=timezone.utc))
                                        break
                                    except Exception:
                                        continue
                    for m in md_re.finditer(t):
                        mon_key = m.group(1).lower()[:3]
                        day = int(m.group(2))
                        mon = month_map.get(mon_key)
                        if mon and 1 <= day <= 31:
                            from datetime import datetime as _dt
                            y = now_utc().year
                            try:
                                out_local.append(_dt(y, mon, day, tzinfo=timezone.utc))
                            except Exception:
                                for yy in range(y, y + 3):
                                    try:
                                        out_local.append(_dt(yy, mon, day, tzinfo=timezone.utc))
                                        break
                                    except Exception:
                                        continue
                except Exception:
                    pass
            except Exception:
                pass
            return out_local

        # try explicit substrings first
        explicit = _explicit_date_substrings(text)
        if explicit:
            # return unique candidates preserving order
            seen = set()
            uniq = []
            for d in explicit:
                key = d.strftime('%Y-%m-%d')
                if key not in seen:
                    seen.add(key)
                    uniq.append(d)
            if uniq:
                return uniq

        settings = {
            'RETURN_AS_TIMEZONE_AWARE': True,
            'TIMEZONE': 'UTC',
            'TO_TIMEZONE': 'UTC',
            # Strict parsing avoids very fuzzy matches
            'STRICT_PARSING': True,
        }
        results = dateparser.search.search_dates(text, settings=settings, languages=['en'])
        if not results:
            # If nothing found, try a conservative fallback: detect day/month
            # patterns without year (e.g., '22/8' or '22-8') and retry by
            # appending the current year. This helps users who enter dates
            # like 'Starfield 22/8' without the year.
            try:
                dm_match = re.search(r"\b(\d{1,2})[./-](\d{1,2})\b", text)
                if dm_match:
                    a = int(dm_match.group(1))
                    b = int(dm_match.group(2))
                    # Interpret ambiguous numeric patterns as day/month (Australian-style)
                    # unless one token is clearly >12 which forces that token to be the day.
                    if a > 12 and 1 <= b <= 12 and 1 <= a <= 31:
                        day, mon = a, b
                    elif b > 12 and 1 <= a <= 12 and 1 <= b <= 31:
                        day, mon = b, a
                    else:
                        # default to day/month (e.g., '5/9' -> 5 Sep)
                        day, mon = a, b
                    # sanity: valid ranges for month/day
                    if 1 <= mon <= 12 and 1 <= day <= 31:
                        # Construct a naive datetime for the current year and mark as UTC
                        year = now_utc().year
                        from datetime import datetime as _dt
                        try:
                            cand = _dt(year, mon, day, tzinfo=timezone.utc)
                            out.append(cand)
                            return out
                        except Exception:
                            # If invalid (e.g., Feb 29 on non-leap year), try next few years
                            for y in range(year, year + 5):
                                try:
                                    cand = _dt(y, mon, day, tzinfo=timezone.utc)
                                    out.append(cand)
                                    return out
                                except Exception:
                                    continue
            except Exception:
                pass
                # Also handle month-name + day patterns like 'Jan 22' or 'January 22'
                # so phrases like 'Event Jan 22' or 'Sep 3' are recognized as
                # yearless matches when dateparser.search misses them under strict parsing.
                try:
                    month_names = {m[:3].lower(): i+1 for i, m in enumerate(MONTHS_EN)}
                    md_match = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2})\b", text)
                    if md_match:
                        mon_name = md_match.group(1).lower()[:3]
                        day = int(md_match.group(2))
                        mon = month_names.get(mon_name)
                        if mon and 1 <= day <= 31:
                            cy = now_utc().year
                            from datetime import datetime as _dt
                            for y in range(cy, cy + 5):
                                try:
                                    cand = _dt(y, mon, day, tzinfo=timezone.utc)
                                    out.append(cand)
                                    return out
                                except Exception:
                                    continue
                    # Also support patterns where day precedes month-name like '4 Sept'
                    dm_match = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\b", text)
                    if dm_match:
                        day = int(dm_match.group(1))
                        mon_name = dm_match.group(2).lower()[:3]
                        mon = month_names.get(mon_name)
                        if mon and 1 <= day <= 31:
                            cy = now_utc().year
                            from datetime import datetime as _dt
                            for y in range(cy, cy + 5):
                                try:
                                    cand = _dt(y, mon, day, tzinfo=timezone.utc)
                                    out.append(cand)
                                    return out
                                except Exception:
                                    continue
                except Exception:
                    pass
                return out
        # If search_dates didn't find anything, also attempt a month-name
        # + day conservative fallback (e.g., 'Sep 3' or '10 Sep') similar to
        # extract_dates_meta. This helps catch short month-name phrases that
        # dateparser.search may miss under STRICT_PARSING.
        try:
            month_names = {m[:3].lower(): i+1 for i, m in enumerate(MONTHS_EN)}
            md_match = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2})\b", text)
            if md_match:
                mon_name = md_match.group(1).lower()[:3]
                day = int(md_match.group(2))
                mon = month_names.get(mon_name)
                if mon and 1 <= day <= 31:
                    cy = now_utc().year
                    from datetime import datetime as _dt
                    for y in range(cy, cy + 5):
                        try:
                            cand = _dt(y, mon, day, tzinfo=timezone.utc)
                            out.append(cand)
                            return out
                        except Exception:
                            continue
        except Exception:
            # If fallback fails, ignore and return whatever we have
            pass
        # Use conservative filtering: skip any matched span that contains a
        # generic anchor token (e.g. 'today') or is a single ambiguous
        # number-word. This prevents spans like '25th today' from producing
        # spurious parsed datetimes.
        if results:
            for match, dt in results:
                if _contains_generic_anchor(match) or (len(match.strip().split()) == 1 and match.strip().lower() in NUMBER_WORDS):
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                out.append(dt.astimezone(timezone.utc))
            if out:
                return out

        # Final flexible fallback: accept short month-name variants (sep, sept,
        # sept.) and both day-month and month-day ordering with optional
        # ordinal suffixes. This targets phrases like '10 Sep', '4 Sept',
        # 'Pay 4 Sept.' which earlier strict parsing missed.
        try:
            # build a pattern that accepts first-3 or full names and optional dot
            month_alts = set()
            for m in MONTHS_EN:
                mlow = m.lower()
                month_alts.add(mlow[:3])
                month_alts.add(mlow)
                month_alts.add(mlow + '.')
                # add common abbreviation 'sept' explicitly
                if mlow.startswith('sep'):
                    month_alts.add('sept')
                    month_alts.add('sept.')
            month_group = r"(?:" + r"|".join(re.escape(x) for x in sorted(month_alts, key=len, reverse=True)) + r")"
            # day before month: '10 Sep' or '10 Sept.' (optionally with st/nd/rd/th)
            dm_re = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + month_group + r"\b", flags=re.IGNORECASE)
            # month before day: 'Sep 10' etc.
            md_re = re.compile(r"\b" + month_group + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b", flags=re.IGNORECASE)
            m = dm_re.search(text)
            if not m:
                m = md_re.search(text)
                if m:
                    # month captured in group 0, day in group 1
                    mon_text = m.group(0).split()[0]
                    day = int(m.group(1))
                else:
                    mon_text = None
                    day = None
            else:
                # dm_re: group1 is day, month may be group2 depending on pattern
                # find trailing token as month
                day = int(m.group(1))
                # attempt to extract month token from match
                mon_match = re.search(r"([A-Za-z]+)\.?$", m.group(0))
                mon_text = mon_match.group(1) if mon_match else None

            if mon_text and day:
                mon_key = mon_text.lower()[:3]
                month_map = {m[:3].lower(): i+1 for i, m in enumerate(MONTHS_EN)}
                mon = month_map.get(mon_key)
                if mon and 1 <= day <= 31:
                    cy = now_utc().year
                    from datetime import datetime as _dt
                    for y in range(cy, cy + 5):
                        try:
                            cand = _dt(y, mon, day, tzinfo=timezone.utc)
                            out.append(cand)
                            return out
                        except Exception:
                            continue
        except Exception:
            pass
        return out
    except Exception:
        logger.exception('extract_dates failed')
        return []


def extract_dates_meta(text: str | None) -> list[dict]:
    """Extract date matches and indicate whether the year was explicit.

    Returns a list of dicts with keys:
      - year_explicit: bool
      - match_text: the substring matched by the parser
      - dt: timezone-aware datetime when year was explicit, else a datetime
            produced by the parser (may have an arbitrary year) for extracting
            time-of-day; callers should prefer month/day when year_explicit is False
      - month: int
      - day: int

    This function does NOT fabricate a year for yearless matches. Yearless
    matches are flagged with year_explicit=False so callers can resolve the
    year using the calendar window or todo creation time.
    """
    if not text:
        return []
    if dateparser is None or dateparser_search is None:
        logger.warning('dateparser not available; extract_dates_meta will return empty list')
        return []
    out: list[dict] = []
    try:
        # Use search_dates (English only) to get matched substring and dt
        settings = {
            'RETURN_AS_TIMEZONE_AWARE': True,
            'TIMEZONE': 'UTC',
            'TO_TIMEZONE': 'UTC',
            'STRICT_PARSING': True,
        }
        results = dateparser.search.search_dates(text, settings=settings, languages=['en'])
        if not results:
            # If nothing found, try conservative DM patterns and attempt parsing
            # by appending the current year to get a parsed dt for month/day
            # phrases while marking the match as yearless so callers can
            # expand across years.
            try:
                # Find all numeric month/day tokens like 5/8, 05-09, etc.
                dm_matches = list(re.finditer(r"\b(\d{1,2})[./-](\d{1,2})\b", text))
                if dm_matches:
                    cy = now_utc().year
                    from datetime import datetime as _dt
                    for dm_match in dm_matches:
                        a = int(dm_match.group(1))
                        b = int(dm_match.group(2))
                        # Interpret ambiguous numeric patterns as day/month (Australian-style)
                        if a > 12 and 1 <= b <= 12 and 1 <= a <= 31:
                            day, mon = a, b
                        elif b > 12 and 1 <= a <= 12 and 1 <= b <= 31:
                            day, mon = b, a
                        else:
                            # default to day/month (e.g., '5/9' -> 5 Sep)
                            day, mon = a, b
                        if 1 <= mon <= 12 and 1 <= day <= 31:
                            # Try to construct a datetime for the current year; if invalid
                            # (e.g., Feb 29 on non-leap year) try the next few years.
                            found_dt = None
                            for y in range(cy, cy + 5):
                                try:
                                    cand = _dt(y, mon, day, tzinfo=timezone.utc)
                                    found_dt = cand
                                    break
                                except Exception:
                                    continue
                            if found_dt:
                                match_text = dm_match.group(0)
                                out.append({'year_explicit': False, 'match_text': match_text, 'dt': found_dt, 'month': found_dt.month, 'day': found_dt.day})
                    if out:
                        return out
                # Also handle month-name + day patterns like 'Jan 22' or 'January 22'
                # so phrases like 'Event Jan 22' are recognized as yearless matches.
                month_names = {m[:3].lower(): i+1 for i, m in enumerate(MONTHS_EN)}
                # Find all month-name + day patterns (e.g., 'May 8', 'May 8th')
                md_matches = list(re.finditer(r"\b([A-Za-z]{3,9})\s+(\d{1,2})\b", text))
                if md_matches:
                    cy = now_utc().year
                    from datetime import datetime as _dt
                    for md_match in md_matches:
                        mon_name = md_match.group(1).lower()[:3]
                        day = int(md_match.group(2))
                        mon = month_names.get(mon_name)
                        if mon and 1 <= day <= 31:
                            found_dt = None
                            for y in range(cy, cy + 5):
                                try:
                                    cand = _dt(y, mon, day, tzinfo=timezone.utc)
                                    found_dt = cand
                                    break
                                except Exception:
                                    continue
                            if found_dt:
                                match_text = md_match.group(0)
                                out.append({'year_explicit': False, 'match_text': match_text, 'dt': found_dt, 'month': found_dt.month, 'day': found_dt.day})
                    if out:
                        return out
            except Exception:
                pass
            return []
        # Filter out generic single-token anchors (e.g., 'now', 'today') which
        # often appear in freeform notes and produce spurious calendar hits.
        GENERIC_ANCHOR_BLACKLIST = {'now', 'today', 'tonight', 'tonite', 'this', 'next'}

        for match_text, dt in results:
            # If the matched substring contains a generic anchor token (e.g.
            # 'today') or is a single ambiguous number-word, skip it. This
            # prevents spans like '25th today' from producing a date.
            if _contains_generic_anchor(match_text) or (len(match_text.strip().split()) == 1 and match_text.strip().lower() in NUMBER_WORDS):
                continue
            # Detect explicit 4-digit year token in the matched substring
            year_present = bool(re.search(r"\b\d{4}\b", match_text))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            # Extract month/day for yearless resolution
            month = dt.month
            day = dt.day
            out.append({'year_explicit': year_present, 'match_text': match_text, 'dt': dt, 'month': month, 'day': day})
        return out
    except Exception:
        logger.exception('extract_dates_meta failed')
        return []


def resolve_yearless_date(month: int, day: int, created_at: datetime, window_start: datetime | None = None, window_end: datetime | None = None) -> list[datetime] | datetime | None:
    """Resolve a yearless month/day into datetime candidates.

    Policy:
    - If a window (window_start/window_end) is provided, return a list of
      candidate datetimes (UTC) for each year that falls inside the window.
    - If no window is provided, choose the single candidate datetime that is
      the earliest occurrence >= created_at. If none found in the next 12
      years, return the nearest future candidate.
    - Handles Feb 29 by skipping non-leap years.

    Returns either a list (for windowed expansion), a single datetime (when
    resolving by creation time), or None if no valid candidate exists.
    """
    from datetime import datetime as _dt

    # normalize created_at to UTC-aware
    try:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = created_at.astimezone(timezone.utc)
    except Exception:
        # if created_at is invalid, fallback to now
        created_at = now_utc()

    def _make_candidate(y: int) -> _dt | None:
        try:
            return _dt(y, int(month), int(day), tzinfo=timezone.utc)
        except Exception:
            return None

    # Compute absolute cap: created_at + 1 year (no leap-year exception)
    try:
        cap_year = created_at.year + 1
        cap_dt = _dt(cap_year, created_at.month, created_at.day, tzinfo=timezone.utc)
    except Exception:
        # fallback: simple year increment at midnight UTC
        cap_dt = _dt(created_at.year + 1, 1, 1, tzinfo=timezone.utc)

    # If a window is provided, return all candidates within it but also
    # cap to at most 1 year after creation (created_at..cap_dt).
    if window_start is not None and window_end is not None:
        try:
            if window_start.tzinfo is None:
                window_start = window_start.replace(tzinfo=timezone.utc)
            else:
                window_start = window_start.astimezone(timezone.utc)
            if window_end.tzinfo is None:
                window_end = window_end.replace(tzinfo=timezone.utc)
            else:
                window_end = window_end.astimezone(timezone.utc)
        except Exception:
            return []
        # Intersect the requested window with the allowed creation-bound window
        allowed_start = max(window_start, created_at)
        allowed_end = min(window_end, cap_dt)
        if allowed_end < allowed_start:
            return []
        yrs = range(allowed_start.year, allowed_end.year + 1)
        out = []
        for y in yrs:
            cand = _make_candidate(y)
            if cand is None:
                continue
            if cand >= allowed_start and cand <= allowed_end:
                out.append(cand)
        return out

    # No window: pick earliest candidate >= created_at but no later than cap_dt
    for y in range(created_at.year, cap_dt.year + 1):
        cand = _make_candidate(y)
        if cand is None:
            continue
        if cand >= created_at and cand <= cap_dt:
            return cand

    # If none found inside the 1-year cap, return None (no leap-year exception)
    return None


def parse_recurrence_phrase(phrase: str) -> dict | None:
    """Parse a short natural-language recurrence phrase into a structured dict.

    Returns a dict with possible keys: freq ('DAILY','WEEKLY','MONTHLY','YEARLY'),
    interval (int), byweekday (list of abbreviations 'MO'..'SU'), bysetpos (int),
    bymonthday (int).
    Returns None if no recurrence phrase detected.
    This is a heuristic parser intended for phrases that immediately follow a date
    like: 'every 2 weeks', 'recurring monthly', 'every 2nd month', 'the 2nd sunday of every month'.
    """
    if not phrase:
        return None
    p = phrase.strip().lower()

    # weekday name map (used by several heuristics below)
    wd_map = {'monday':'MO','tuesday':'TU','wednesday':'WE','thursday':'TH','friday':'FR','saturday':'SA','sunday':'SU'}
    # common simple patterns
    m = re.search(r'every\s+(\d+)\s*(day|week|month|year)s?', p)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        freq_map = {'day': 'DAILY', 'week': 'WEEKLY', 'month': 'MONTHLY', 'year': 'YEARLY'}
        return {'freq': freq_map.get(unit, 'DAILY'), 'interval': n}

    # every other week / every other month
    m = re.search(r'every\s+other\s+(week|month|day|year)', p)
    if m:
        unit = m.group(1)
        freq_map = {'day': 'DAILY', 'week': 'WEEKLY', 'month': 'MONTHLY', 'year': 'YEARLY'}
        return {'freq': freq_map.get(unit, 'WEEKLY'), 'interval': 2}

    # recurring monthly / recurring weekly
    m = re.search(r'recurring\s+(daily|weekly|monthly|yearly)', p)
    if m:
        word = m.group(1)
        word_map = {'daily': 'DAILY', 'weekly': 'WEEKLY', 'monthly': 'MONTHLY', 'yearly': 'YEARLY'}
        return {'freq': word_map.get(word)}

    # every Nth month (e.g., 'every 2nd month' -> interval 2 monthly)
    m = re.search(r'every\s+(\d+)(?:st|nd|rd|th)?\s+month', p)
    if m:
        return {'freq': 'MONTHLY', 'interval': int(m.group(1))}

    # patterns like 'every 2nd tuesday' meaning weekly with interval and weekday
    m = re.search(r'every\s+(\d+)(?:st|nd|rd|th)?\s+([a-z]+)s?', p)
    if m:
        n = int(m.group(1))
        dayname = m.group(2)
        for full, abbr in wd_map.items():
            if full.startswith(dayname):
                # interpret as every n-th week on weekday (interval n weekly)
                return {'freq': 'WEEKLY', 'interval': n, 'byweekday': [abbr]}

    # 'every weekday' -> MON-FRI weekly with byweekday
    if 'every weekday' in p or 'weekdays' in p:
        return {'freq': 'WEEKLY', 'byweekday': ['MO','TU','WE','TH','FR']}

    # day names: 'every monday' or 'on monday(s)'
    m = re.search(r'(?:every|on)\s+((?:mon|tues|wednes|thurs|fri|satur|sun)[a-z]*)s?', p)
    if m:
        name = m.group(1)
        # normalize full weekday name by checking prefixes
        for full, abbr in wd_map.items():
            if full.startswith(name) or full == name:
                return {'freq': 'WEEKLY', 'byweekday': [abbr]}

    # 'the 2nd sunday of every month' -> byweekday SU with bysetpos 2
    m = re.search(r'the\s+(\d+)(?:st|nd|rd|th)?\s+([a-z]+)s?\s+of\s+every\s+month', p)
    if m:
        pos = int(m.group(1))
        dayname = m.group(2)
        for full, abbr in wd_map.items():
            if full.startswith(dayname):
                return {'freq': 'MONTHLY', 'byweekday': [abbr], 'bysetpos': pos}

    # also accept 'every 2nd sunday of every month' (without leading 'the')
    m = re.search(r'every\s+(\d+)(?:st|nd|rd|th)?\s+([a-z]+)s?\s+of\s+every\s+month', p)
    if m:
        pos = int(m.group(1))
        dayname = m.group(2)
        for full, abbr in wd_map.items():
            if full.startswith(dayname):
                return {'freq': 'MONTHLY', 'byweekday': [abbr], 'bysetpos': pos}

    # 'the last friday of every month' -> byweekday FR with bysetpos -1
    m = re.search(r'(?:the\s+)?last\s+([a-z]+)s?\s+of\s+every\s+month', p)
    if m:
        dayname = m.group(1)
        for full, abbr in wd_map.items():
            if full.startswith(dayname):
                return {'freq': 'MONTHLY', 'byweekday': [abbr], 'bysetpos': -1}

    # 'last day of every month' -> bymonthday = -1 (last day)
    m = re.search(r'last\s+day\s+of\s+every\s+month', p)
    if m:
        return {'freq': 'MONTHLY', 'bymonthday': -1}

    # fallback: look for 'every (day|week|month|year)'
    m = re.search(r'every\s+(day|week|month|year)', p)
    if m:
        unit = m.group(1)
        freq_map = {'day': 'DAILY', 'week': 'WEEKLY', 'month': 'MONTHLY', 'year': 'YEARLY'}
        return {'freq': freq_map.get(unit)}

    # 'every month on the 1st' or 'every month on the 1st' -> bymonthday
    m = re.search(r'every\s+month(?:\s+on)?\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?', p)
    if m:
        return {'freq': 'MONTHLY', 'bymonthday': int(m.group(1))}

    return None


# --- Occurrence/ignore hashing helpers ---
def _norm_iso_for_hash(dt):
    """Return canonical ISO Z string for hashing; accept str or datetime."""
    if dt is None:
        return ''
    if isinstance(dt, str):
        s = dt.replace('Z', '+00:00')
        try:
            d = datetime.fromisoformat(s)
        except Exception:
            return dt.strip()
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    return str(dt)


def _canonical_json(obj):
    import json
    return json.dumps(obj, separators=(',', ':'), sort_keys=True, ensure_ascii=False)


def _sha256_hex(s):
    from hashlib import sha256
    return sha256(s.encode('utf-8')).hexdigest()


def occurrence_hash(item_type, item_id, occurrence_dt, rrule=None, title=None):
    payload = {
        'type': str(item_type),
        'id': str(item_id),
        'dt': _norm_iso_for_hash(occurrence_dt),
        'rrule': rrule or '',
        'title': (title or '').strip().lower()
    }
    cj = _canonical_json(payload)
    return 'occ:' + _sha256_hex(cj)


def ignore_list_hash(list_id, owner_id=None):
    payload = {'op': 'list', 'list_id': str(list_id), 'owner_id': str(owner_id) if owner_id is not None else ''}
    cj = _canonical_json(payload)
    return 'ign:list:' + _sha256_hex(cj)


def ignore_todo_from_hash(todo_id, from_occurrence_dt):
    payload = {'op': 'todo_from', 'todo_id': str(todo_id), 'from_dt': _norm_iso_for_hash(from_occurrence_dt)}
    cj = _canonical_json(payload)
    return 'ign:todo:' + _sha256_hex(cj)

    # weekday name map (used by several heuristics below)
    wd_map = {'monday':'MO','tuesday':'TU','wednesday':'WE','thursday':'TH','friday':'FR','saturday':'SA','sunday':'SU'}
    # common simple patterns
    m = re.search(r'every\s+(\d+)\s*(day|week|month|year)s?', p)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        freq_map = {'day': 'DAILY', 'week': 'WEEKLY', 'month': 'MONTHLY', 'year': 'YEARLY'}
        return {'freq': freq_map.get(unit, 'DAILY'), 'interval': n}

    # every other week / every other month
    m = re.search(r'every\s+other\s+(week|month|day|year)', p)
    if m:
        unit = m.group(1)
        freq_map = {'day': 'DAILY', 'week': 'WEEKLY', 'month': 'MONTHLY', 'year': 'YEARLY'}
        return {'freq': freq_map.get(unit, 'WEEKLY'), 'interval': 2}

    # recurring monthly / recurring weekly
    m = re.search(r'recurring\s+(daily|weekly|monthly|yearly)', p)
    if m:
        word = m.group(1)
        word_map = {'daily': 'DAILY', 'weekly': 'WEEKLY', 'monthly': 'MONTHLY', 'yearly': 'YEARLY'}
        return {'freq': word_map.get(word)}

    # every Nth month (e.g., 'every 2nd month' -> interval 2 monthly)
    m = re.search(r'every\s+(\d+)(?:st|nd|rd|th)?\s+month', p)
    if m:
        return {'freq': 'MONTHLY', 'interval': int(m.group(1))}

    # patterns like 'every 2nd tuesday' meaning weekly with interval and weekday
    m = re.search(r'every\s+(\d+)(?:st|nd|rd|th)?\s+([a-z]+)s?', p)
    if m:
        n = int(m.group(1))
        dayname = m.group(2)
        for full, abbr in wd_map.items():
            if full.startswith(dayname):
                # interpret as every n-th week on weekday (interval n weekly)
                return {'freq': 'WEEKLY', 'interval': n, 'byweekday': [abbr]}

    # 'every weekday' -> MON-FRI weekly with byweekday
    if 'every weekday' in p or 'weekdays' in p:
        return {'freq': 'WEEKLY', 'byweekday': ['MO','TU','WE','TH','FR']}

    # day names: 'every monday' or 'on monday(s)'
    wd_map = {'monday':'MO','tuesday':'TU','wednesday':'WE','thursday':'TH','friday':'FR','saturday':'SA','sunday':'SU'}
    m = re.search(r'(?:every|on)\s+((?:mon|tues|wednes|thurs|fri|satur|sun)[a-z]*)s?', p)
    if m:
        name = m.group(1)
        # normalize full weekday name by checking prefixes
        for full, abbr in wd_map.items():
            if full.startswith(name) or full == name:
                return {'freq': 'WEEKLY', 'byweekday': [abbr]}

    # 'the 2nd sunday of every month' -> byweekday SU with bysetpos 2
    m = re.search(r'the\s+(\d+)(?:st|nd|rd|th)?\s+([a-z]+)s?\s+of\s+every\s+month', p)
    if m:
        pos = int(m.group(1))
        dayname = m.group(2)
        for full, abbr in wd_map.items():
            if full.startswith(dayname):
                return {'freq': 'MONTHLY', 'byweekday': [abbr], 'bysetpos': pos}

    # also accept 'every 2nd sunday of every month' (without leading 'the')
    m = re.search(r'every\s+(\d+)(?:st|nd|rd|th)?\s+([a-z]+)s?\s+of\s+every\s+month', p)
    if m:
        pos = int(m.group(1))
        dayname = m.group(2)
        for full, abbr in wd_map.items():
            if full.startswith(dayname):
                return {'freq': 'MONTHLY', 'byweekday': [abbr], 'bysetpos': pos}

    # 'the last friday of every month' -> byweekday FR with bysetpos -1
    m = re.search(r'(?:the\s+)?last\s+([a-z]+)s?\s+of\s+every\s+month', p)
    if m:
        dayname = m.group(1)
        for full, abbr in wd_map.items():
            if full.startswith(dayname):
                return {'freq': 'MONTHLY', 'byweekday': [abbr], 'bysetpos': -1}

    # 'last day of every month' -> bymonthday = -1 (last day)
    m = re.search(r'last\s+day\s+of\s+every\s+month', p)
    if m:
        return {'freq': 'MONTHLY', 'bymonthday': -1}

    # fallback: look for 'every (day|week|month|year)'
    m = re.search(r'every\s+(day|week|month|year)', p)
    if m:
        unit = m.group(1)
        freq_map = {'day': 'DAILY', 'week': 'WEEKLY', 'month': 'MONTHLY', 'year': 'YEARLY'}
        return {'freq': freq_map.get(unit)}

    # 'every month on the 1st' or 'every month on the 1st' -> bymonthday
    m = re.search(r'every\s+month(?:\s+on)?\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?', p)
    if m:
        return {'freq': 'MONTHLY', 'bymonthday': int(m.group(1))}

    return None


def parse_date_and_recurrence(text: str) -> tuple[datetime | None, dict | None]:
    """Find the first date in text and parse an immediately following recurrence phrase.

    Returns (dtstart, recurrence_dict) where recurrence_dict is the output of
    `parse_recurrence_phrase`. dtstart is timezone-aware UTC datetime. Returns
    (None, None) if no date found.
    """
    if not text:
        return None, None
    if dateparser is None or dateparser_search is None:
        return None, None
    try:
        # Restrict language to English to avoid costly language detection.
        # Prefer seeded DateDataParser for single-date extraction.
        matched_text = None
        dt = None
        results = None

        if _DATE_DATA_PARSER is not None:
            res = _DATE_DATA_PARSER.get_date_data(text)
            dt = getattr(res, 'date_obj', None)
            # Guard against single-token number-words being interpreted as
            # months/days (e.g., 'eight' -> August). If so, ignore the
            # DateDataParser result and fall back to search_dates.
            token = text.strip().lower()
            if dt and ((token in NUMBER_WORDS) or re.fullmatch(r"\d{1,2}", token)):
                dt = None

        # If DateDataParser produced a datetime, try to locate a textual span
        # using a lightweight English-focused regex. If that fails, fall back
        # to dateparser.search.search_dates to obtain a matched span.
        if dt is not None:
            # basic patterns: ISO, numeric, and English month names
            month_names = '|'.join([re.escape(m) for m in MONTHS_EN])
            span_re = re.compile(r'(\d{4}-\d{1,2}-\d{1,2})|(\d{1,2}[./]\d{1,2}[./]\d{2,4})|(' + month_names + r')', re.IGNORECASE)
            m = span_re.search(text)
            if m:
                matched_text = m.group(0)
                # synthesize results shape to reuse downstream logic
                results = [(matched_text, dt)]
            else:
                # last-resort: use search_dates to get an exact matched span
                results = dateparser.search.search_dates(
                    text,
                    settings={'RETURN_AS_TIMEZONE_AWARE': True, 'TIMEZONE': 'UTC', 'TO_TIMEZONE': 'UTC', 'STRICT_PARSING': True},
                    languages=['en'],
                )
                if results:
                    matched_text, parsed_dt = results[0]
                    if dt is None:
                        dt = parsed_dt
        else:
            # No dt from DateDataParser: fall back to full search
            results = dateparser.search.search_dates(
                text,
                settings={'RETURN_AS_TIMEZONE_AWARE': True, 'TIMEZONE': 'UTC', 'TO_TIMEZONE': 'UTC', 'STRICT_PARSING': True},
                languages=['en'],
            )
            if not results:
                return None, None

        if not results:
            return None, None

        # take first match
        matched_text, dt = results[0]

        # If the matched_text contains a generic anchor token (e.g. 'today' or
        # 'now'), treat it as non-match to avoid interpreting casual words
        # (and mixed spans like '25th today') as explicit date anchors.
        if _contains_generic_anchor(matched_text):
            return None, None

        # find its position in the original text to extract the following phrase
        # Only examine a short immediate span after the matched date to avoid
        # accidentally treating unrelated later text (for example notes that
        # mention a weekday) as a recurrence phrase.
        idx = text.lower().find(matched_text.lower())
        if idx == -1:
            # fallback: can't find span
            tail = text
        else:
            tail = text[idx + len(matched_text):]

        # Normalize and trim leading separators
        tail = tail.lstrip(" ,;:-\t\n")
        # Prefer the first sentence/segment only — split on common sentence
        # terminators and take the first segment. Also cap length to avoid
        # parsing extremely long notes.
        tail_segment = re.split(r'[\r\n\.\!\?;]', tail, 1)
        tail_segment = (tail_segment[0] if tail_segment else tail)[:120].strip()

        rec = parse_recurrence_phrase(tail_segment)

        # Heuristic: if recurrence is DAILY (every day) and the tail does not
        # contain an explicit time or date anchor, treat it as non-recurring.
        if rec and rec.get('freq') == 'DAILY':
            # Use the same limited segment when checking for explicit time/date
            # anchors so that later mentions don't influence the decision.
            if not _has_time_or_date_anchor(tail_segment):
                # suppress ambiguous every-day recurrence unless a time/date is present
                rec = None

        # ensure dt is timezone-aware UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt, rec
    except Exception:
        logger.exception('parse_date_and_recurrence failed')
        return None, None


def recurrence_dict_to_rrule_params(rec: dict) -> dict:
    """Convert a recurrence dict (from parse_recurrence_phrase) into
    kwargs suitable for dateutil.rrule. Returns a dict mapping keys like
    'freq','interval','byweekday','bysetpos','bymonthday' to rrule-compatible
    values.

    Example input: {'freq':'WEEKLY','interval':2,'byweekday':['MO']}
    Output will map 'freq' to dateutil.rrule.WEEKLY and byweekday to a tuple
    containing dateutil.rrule weekday objects.
    """
    if not rec:
        return {}
    try:
        from dateutil import rrule as _rrule
    except Exception:
        raise
    out: dict = {}
    freq_map = {'DAILY': _rrule.DAILY, 'WEEKLY': _rrule.WEEKLY, 'MONTHLY': _rrule.MONTHLY, 'YEARLY': _rrule.YEARLY}
    f = rec.get('freq')
    if f:
        out['freq'] = freq_map.get(f.upper(), _rrule.DAILY)
    if 'interval' in rec:
        out['interval'] = int(rec['interval'])
    if 'bymonthday' in rec:
        out['bymonthday'] = int(rec['bymonthday'])
    if 'bysetpos' in rec:
        out['bysetpos'] = int(rec['bysetpos'])
    if 'byweekday' in rec:
        # convert strings like 'MO','TU' into dateutil weekday objects
        wd_map = {'MO': _rrule.MO, 'TU': _rrule.TU, 'WE': _rrule.WE, 'TH': _rrule.TH, 'FR': _rrule.FR, 'SA': _rrule.SA, 'SU': _rrule.SU}
        vals = []
        for w in rec.get('byweekday'):
            if isinstance(w, str):
                key = w.upper()
                if key in wd_map:
                    vals.append(wd_map[key])
            else:
                # assume already a weekday object or integer
                vals.append(w)
        if vals:
            out['byweekday'] = tuple(vals)
    return out


def build_rrule_from_recurrence(rec: dict, dtstart: datetime):
    """Build a dateutil.rrule.rrule object from the recurrence dict and a dtstart.

    Returns an rrule instance. Raises ImportError if dateutil is not available.
    """
    params = recurrence_dict_to_rrule_params(rec)
    try:
        from dateutil import rrule as _rrule
    except Exception:
        raise
    # ensure dtstart is timezone-aware; dateutil handles tz-aware datetimes
    return _rrule.rrule(dtstart=dtstart, **params)


def recurrence_dict_to_rrule_string(rec: dict) -> str:
    """Export a recurrence dict to an RFC5545 RRULE string.

    Supports keys: freq (DAILY/WEEKLY/MONTHLY/YEARLY), interval, byweekday
    (list of 'MO'..'SU' or dateutil weekday objects), bysetpos, bymonthday.
    Returns a semicolon-separated RRULE value (no leading 'RRULE:').
    """
    if not rec:
        return ''
    parts: list[str] = []
    # FREQ
    f = rec.get('freq')
    if f:
        parts.append(f'FREQ={f.upper()}')
    # INTERVAL
    if 'interval' in rec and rec.get('interval') is not None:
        parts.append(f'INTERVAL={int(rec["interval"])}')
    # BYMONTHDAY
    if 'bymonthday' in rec and rec.get('bymonthday') is not None:
        parts.append(f'BYMONTHDAY={int(rec["bymonthday"])}')
    # BYSETPOS
    if 'bysetpos' in rec and rec.get('bysetpos') is not None:
        parts.append(f'BYSETPOS={int(rec["bysetpos"])}')
    # BYDAY / byweekday
    if 'byweekday' in rec and rec.get('byweekday'):
        vals = []
        try:
            from dateutil import rrule as _rrule
            wd_map = {'MO': 'MO', 'TU': 'TU', 'WE': 'WE', 'TH': 'TH', 'FR': 'FR', 'SA': 'SA', 'SU': 'SU'}
        except Exception:
            wd_map = {'MO': 'MO', 'TU': 'TU', 'WE': 'WE', 'TH': 'TH', 'FR': 'FR', 'SA': 'SA', 'SU': 'SU'}
        for w in rec.get('byweekday'):
            if isinstance(w, str):
                vals.append(w.upper())
            else:
                # dateutil weekday object (e.g., MO) has attribute weekday
                try:
                    # weekday objects when str() produce 'MO' etc, but support .weekday
                    s = str(w)
                    vals.append(s.upper())
                except Exception:
                    # fallback: try int
                    vals.append(str(w))
        if vals:
            parts.append('BYDAY=' + ','.join(vals))
    return ';'.join(parts)


def parse_text_to_rrule_string(text: str) -> tuple[datetime | None, str]:
    """Parse freeform text for an anchor date followed by a recurrence phrase.

    Returns a tuple (dtstart, rrule_string). dtstart is a timezone-aware UTC
    datetime or None if no date found. rrule_string is the RRULE body (no
    leading 'RRULE:') or empty string if no recurrence phrase was found.

    Example: '2025-08-25 every 2 weeks' -> (datetime(2025,8,25,...), 'FREQ=WEEKLY;INTERVAL=2')
    """
    if not text:
        return None, ''
    dt, rec = parse_date_and_recurrence(text)
    # If no date anchor found, attempt to parse a recurrence phrase from the
    # whole text. If a recurrence is found we synthesize a dtstart using
    # now_utc() (optionally honoring explicit time like 'at 9am').
    if dt is None:
        # If no explicit date anchor was found, attempt to parse a recurrence
        # phrase from the whole text. If a recurrence phrase is found we
        # synthesize a dtstart using now_utc(). Also honor simple time tokens
        # like 'at 9am' to set the time portion on the synthesized dtstart.
        try:
            rec = parse_recurrence_phrase(text)
        except Exception:
            rec = None
        if rec:
            # Decide whether to synthesize a dtstart. We only synthesize when
            # the input contains other content besides a bare recurrence
            # phrase. For example, 'Gym every other day' should synthesize
            # (has 'Gym'), but 'every week' should not.
            txt = text.strip().lower()
            # simple tokenization
            tokens = re.findall(r"[\w']+", txt)
            RECURRENCE_KEYWORDS = {
                'every','other','recurring','daily','weekly','monthly','yearly',
                'day','week','month','year','weekday','weekdays','the','last','on','of','month',
                'monday','tuesday','wednesday','thursday','friday','saturday','sunday',
                'first','second','third','1st','2nd','3rd','st','nd','rd','th','interval',
            }
            # If there exists any token that's not purely a recurrence keyword
            # or a numeric/ordinal token, we consider this non-bare and
            # synthesize a dtstart.
            nonrec_tokens = [t for t in tokens if t not in RECURRENCE_KEYWORDS and not re.fullmatch(r"\d+", t)]
            if not nonrec_tokens:
                # bare recurrence phrase like 'every week' -> do not synthesize
                return None, ''
            # Heuristic: for 'every day' recurrences without an explicit
            # date or time anchor, only block synthesis when the phrase is
            # essentially bare; if the text contains other tokens (e.g., a
            # task name) we will synthesize dtstart. This allows inputs like
            # 'Gym every other day' or 'Exercise every day' to produce a
            # synthesized dtstart while still avoiding synthesis for bare
            # phrases like 'every day'.
            if rec.get('freq') == 'DAILY' and not _has_time_or_date_anchor(text) and not nonrec_tokens:
                return None, ''

            # synthesize dtstart as current UTC time, but allow overriding
            # the time when an explicit 'at HH[:MM](am|pm)?' token is present
            dt = now_utc()
            try:
                m = re.search(r"at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, flags=re.IGNORECASE)
                if m:
                    hour = int(m.group(1))
                    minute = int(m.group(2) or 0)
                    ampm = m.group(3)
                    if ampm:
                        if ampm.lower() == 'pm' and hour != 12:
                            hour += 12
                        if ampm.lower() == 'am' and hour == 12:
                            hour = 0
                    # Replace time on the UTC dt
                    dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
            except Exception:
                # If anything goes wrong parsing the time, keep now_utc()
                dt = now_utc()
            # proceed to build rrule string below using synthesized dt
        else:
            return None, ''

    # fallback: if a date anchor was found but no recurrence dict returned,
    # try scanning the whole text for a recurrence phrase.
    if not rec:
        try:
            rec = parse_recurrence_phrase(text)
        except Exception:
            rec = None
    if not rec:
        return dt, ''
    rrule_str = recurrence_dict_to_rrule_string(rec)
    return dt, rrule_str


def parse_text_to_rrule(text: str) -> tuple[object | None, datetime | None]:
    """Parse freeform text for an anchor date followed by a recurrence phrase
    and return (rrule_obj, dtstart).

    - If a date and recurrence are found, returns (rrule.rrule instance, dtstart).
    - If a date is found but no recurrence, returns (None, dtstart).
    - If no date found, returns (None, None).
    """
    if not text:
        return None, None
    dt, rec = parse_date_and_recurrence(text)
    # If no date anchor, try to parse recurrence from whole text and synthesize dt
    if dt is None:
        # If there's no explicit anchor date, do not synthesize a dt/rrule here.
        # Higher-level helpers may synthesize when desired (e.g., parse_text_to_rrule_string),
        # but parse_text_to_rrule should return (None, None) for pure recurrence phrases.
        return None, None

    # If we have a recurrence dict missing (date present but rec missing),
    # do NOT scan the entire text for recurrence phrases. Scanning the whole
    # note may pick up unrelated later mentions (e.g., 'Saturday' in a
    # follow-up sentence). Higher-level callsites that intentionally want
    # to synthesize an rrule from a bare recurrence phrase should use the
    # more permissive helpers. Here, prefer conservatism: return (None, dt)
    # when no immediate recurrence dict was found by parse_date_and_recurrence.
    if rec is None:
        return None, dt
    try:
        r = build_rrule_from_recurrence(rec, dt)
        return r, dt
    except Exception:
        logger.exception('failed to build rrule from recurrence')
        return None, dt
