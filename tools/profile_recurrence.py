"""Run cProfile against recurrence parsing helpers to locate hotspots.

Usage:
    .venv/bin/python tools/profile_recurrence.py

It loads a set of representative texts and profiles `extract_dates` and
`parse_text_to_rrule` from `app.utils`.
"""
import cProfile
import pstats
import io
from app.utils import extract_dates, parse_text_to_rrule

SAMPLE_TEXTS = [
    "tomorrow",
    "every day at 9am",
    "every weekday",
    "every month on the 1st",
    "every 3rd tuesday",
    "2025-09-05 do something",
    "Pay rent every month on the 1st",
    "Meeting every Monday 10am",
    "call mom in 2 weeks",
]

pr = cProfile.Profile()
pr.enable()
for t in SAMPLE_TEXTS * 200:
    try:
        extract_dates(t)
    except Exception:
        pass
    try:
        parse_text_to_rrule(t)
    except Exception:
        pass
pr.disable()

s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats('tottime')
ps.print_stats(60)
print(s.getvalue())
