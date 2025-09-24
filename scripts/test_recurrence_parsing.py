#!/usr/bin/env python3

"""
Load data/recurrence_samples.json and run the todo text date/recurrence parser
for each sample. Outputs a JSON and optional CSV with per-item results and a
summary.

Usage:
  python scripts/test_recurrence_parsing.py \
    --input data/recurrence_samples.json \
    --out data/recurrence_parse_results.json \
    --csv data/recurrence_parse_results.csv
"""

# (primary), with app.utils.extract_dates_meta and a fallback to app.utils.parse_recurrence_phrase; recurrence_dict_to_rrule_string is used to render an RRULE string.

# Question: what function in the web app injects the dtstart with the todo modification date if one isn't specified in the todo text or note istelf?

#It’s synthesized in app.utils.parse_text_to_rrule_string. When there’s a recurrence phrase but no explicit date anchor, that function creates a dtstart using now_utc().
#The web endpoints that use it are _create_todo_internal and _update_todo_internal in app.main: they call parse_text_to_rrule_string(...) and then persist the returned dtstart into todo.recurrence_dtstart.
#It doesn’t read modified_at directly. However, update_todo sets modified_at = now_utc() right after, so dtstart and modified_at usually match in practice because both come from now_utc() at that moment.

#in the test script test_recurrence_parsing.py are date anchors for 'recurrence+data' created randomly?
#No. In that script, “recurrence+date” only occurs when parse_date_and_recurrence finds an explicit date anchor in the input text. The script doesn’t generate random anchors or synthesize dtstart for classification; if no date is found, it stays “recurrence-only.”

#have a look at id 3 , where does the parsed_dt come from?
#From app.utils.parse_date_and_recurrence(text). The script sets parsed_dt = dt returned by that function (then ISO-serialized). That dt is derived from any explicit date found in the text (numeric like 5/9, 2025‑09‑05, or month‑name forms). For yearless matches it uses the current year; all results are normalized to UTC. It’s not taken from modified_at, not synthesized by the test script, and not based on DEFAULT_TIMEZONE.

#It comes from the fallback in app.utils.parse_date_and_recurrence: when no explicit date anchor is found, it calls dateparser’s DateDataParser on the whole phrase, which can produce a relative, “now”-anchored datetime. That returned datetime (normalized to UTC) is what your script records as parsed_dt. It’s not synthesized by the test script, not taken from modified_at, and not from DEFAULT_TIMEZONE—it's DateDataParser interpreting “every 3 days after work” relative to the current time.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _ensure_project_on_path():
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_project_on_path()

from app.utils import (  # noqa: E402
    extract_dates_meta,
    parse_date_and_recurrence,
    parse_recurrence_phrase,
    recurrence_dict_to_rrule_string,
)


def safe_iso(dt) -> str | None:
    try:
        if dt is None:
            return None
        return dt.isoformat()
    except Exception:
        return None


def json_safe(obj):
    """Recursively convert objects to JSON-serializable forms."""
    import datetime as _dt
    try:
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
            # prefer ISO
            try:
                return obj.isoformat()
            except Exception:
                return str(obj)
        if isinstance(obj, dict):
            return {str(k): json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [json_safe(v) for v in obj]
        # dateutil weekdays and similar types -> str
        s = str(obj)
        return s
    except Exception:
        try:
            return str(obj)
        except Exception:
            return None


def classify_result(dt, rec, plain_metas: List[dict]) -> str:
    if dt is not None and rec:
        return "recurrence+date"
    if rec and dt is None:
        return "recurrence-only"
    if (plain_metas or []) and not rec:
        return "date-only"
    if (plain_metas or []) and rec:
        return "recurrence+plain-dates"
    return "none"


def run_one(text: str) -> Dict[str, Any]:
    # parse anchor date + recurrence (primary)
    dt, rec = parse_date_and_recurrence(text)
    # extract plain dates (secondary)
    plain = extract_dates_meta(text)
    # derive a recurrence string if rec exists
    rrule = recurrence_dict_to_rrule_string(rec or {}) if rec else ""
    # fallback: try recurrence-only if parse_date_and_recurrence didn't find any
    if not rec:
        rec_only = parse_recurrence_phrase(text)
    else:
        rec_only = None
    kind = classify_result(dt, rec or rec_only, plain)
    out: Dict[str, Any] = {
        "parsed_dt": safe_iso(dt),
        # JSON friendly recurrence dict
        "rec": json_safe(rec or rec_only) if (rec or rec_only) else None,
        "rrule": rrule if rrule else (recurrence_dict_to_rrule_string(rec_only) if rec_only else ""),
        "plain_dates_meta": json_safe(plain or []),
        "kind": kind,
    }
    return out


def main():
    ap = argparse.ArgumentParser(description="Test parsing on generated samples")
    ap.add_argument("--input", default="data/recurrence_samples.json", help="input JSON file")
    ap.add_argument("--out", default="data/recurrence_parse_results.json", help="output JSON file")
    ap.add_argument("--csv", default="", help="optional CSV output path")
    ap.add_argument("--limit", type=int, default=0, help="limit number of items (0=all)")
    args = ap.parse_args()

    src = Path(args.input)
    data = json.loads(src.read_text(encoding="utf-8"))
    items = data.get("items") or []
    if args.limit and args.limit > 0:
        items = items[: args.limit]

    results: List[Dict[str, Any]] = []
    counts = {
        "total": 0,
        "recurrence+date": 0,
        "recurrence-only": 0,
        "date-only": 0,
        "recurrence+plain-dates": 0,
        "none": 0,
    }

    for it in items:
        text = it.get("text", "")
        rid = it.get("id")
        core = it.get("core")
        parsed = run_one(text)
        row = {
            "id": rid,
            "text": text,
            "core": core,
            **parsed,
        }
        results.append(row)
        counts["total"] += 1
        counts[parsed["kind"]] += 1

    out_json = {
        "input": str(src),
        "count": len(results),
        "summary": counts,
        "items": results,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.csv:
        import csv

        csv_path = Path(args.csv)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "id",
            "kind",
            "parsed_dt",
            "rrule",
            "text",
            "core",
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in results:
                w.writerow({k: r.get(k, "") for k in fields})

    # Print concise summary
    print("Summary:")
    for k in [
        "total",
        "recurrence+date",
        "recurrence-only",
        "date-only",
        "recurrence+plain-dates",
        "none",
    ]:
        print(f"  {k}: {counts[k]}")
    print(f"Wrote JSON: {out_path}")
    if args.csv:
        print(f"Wrote CSV: {args.csv}")


if __name__ == "__main__":
    main()
