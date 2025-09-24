#!/usr/bin/env python3
"""
Generate a JSON file with mixed recurrence phrases and plain dates in various formats.

Outputs data/recurrence_samples.json with N (default 1000) items.
Each item has: {"id": int, "text": str}
"""
from __future__ import annotations
import json
import random
from datetime import date, timedelta
from pathlib import Path


PREFIXES = [
    "Odd jobs:", "Errands:", "Reminder:", "TODO:", "Chore:", "Task:",
    "Note:", "Follow up:", "Next:", "Plan:", "Groceries:", "Bills:",
]

POSTFIXES = [
    "— before lunch", " (evening)", " after work", " — asap", " #home",
    " #work", " at Bunnings", " with John", " for project X", " (low)",
]

RECURRENCE_PATTERNS = [
    # Basic natural recurrences
    "every day", "every weekday", "every weekend",
    "every Monday", "every Tuesday", "every Wednesday", "every Thursday", "every Friday",
    "every Saturday", "every Sunday",
    "every 2 days", "every 3 days", "every 4 days", "every 5 days",
    "every 2 weeks", "every 3 weeks", "every 4 weeks",
    "every month", "every 2 months", "every quarter", "every year",
    # With on/at
    "every week on Monday", "every month on the 1st", "every year on 25/12",
    # Australian-like expressions
    "each fortnight", "every second day", "every second week",
]


def iso_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def au_date(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def us_date(d: date) -> str:
    return d.strftime("%m/%d/%Y")


def random_date(within_days: int = 365) -> date:
    today = date.today()
    delta = random.randint(-within_days, within_days)
    return today + timedelta(days=delta)


def random_plain_date() -> str:
    d = random_date()
    fmt = random.choice([iso_date, au_date, us_date])
    return fmt(d)


def random_recurrence_or_date() -> str:
    if random.random() < 0.6:
        # Recurrence majority
        base = random.choice(RECURRENCE_PATTERNS)
        # Occasionally append a time or day
        extras = [
            " at 9am", " at 5:30pm", " on weekdays", " on weekends", " starting " + random_plain_date(),
        ]
        if random.random() < 0.35:
            base += random.choice(extras)
        return base
    else:
        return random_plain_date()


def make_sentence(core: str) -> str:
    prefix = random.choice(PREFIXES) if random.random() < 0.8 else ""
    postfix = random.choice(POSTFIXES) if random.random() < 0.7 else ""
    subject = random.choice([
        "mow the lawn", "pay electricity bill", "water the plants", "email client",
        "backup photos", "buy milk", "wash car", "call plumber", "book dentist",
        "update spreadsheet", "clean garage", "review PR",
    ])
    # Mix where the core phrase appears: start, middle, end
    pattern = random.choice(["start", "middle", "end"])
    parts = []
    if prefix:
        parts.append(prefix)
    if pattern == "start":
        parts.append(core)
        parts.append(subject)
    elif pattern == "middle":
        parts.append(subject)
        parts.append(core)
    else:  # end
        parts.append(subject)
        parts.append(core)
    if postfix:
        parts.append(postfix)
    # Ensure single spaces
    return " ".join(p.strip() for p in parts if p).replace("  ", " ").strip()


def generate(n: int = 1000):
    items = []
    for i in range(1, n + 1):
        core = random_recurrence_or_date()
        text = make_sentence(core)
        items.append({"id": i, "text": text, "core": core})
    return items


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Generate recurrence/date dataset")
    ap.add_argument("-n", "--num", type=int, default=1000, help="number of items")
    ap.add_argument("-o", "--out", default="data/recurrence_samples.json", help="output path")
    args = ap.parse_args()

    items = generate(args.num)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump({"count": len(items), "items": items}, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(items)} items to {out_path}")


if __name__ == "__main__":
    main()
