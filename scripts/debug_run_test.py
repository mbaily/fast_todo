"""Run the specific failing pytest, capture its output lines into Python lists,
write those to files (overwrite), then extract calendar debug lines from
.pytest_debug_output/filtered_calendar_debug.txt to narrow down the year-resolution logic.

This script avoids shell tools and heredocs; it uses Python only.
"""
import sys
import json
import subprocess
import re
from pathlib import Path

TEST_PATH = "tests/test_year_resolution_integration.py::test_calendar_window_prefers_window_candidates"
OUT_JSON = Path("scripts/debug_run_test_output.json")
OUT_TXT = Path("scripts/debug_run_test_output.txt")
FILTERED_LOG = Path(".pytest_debug_output/filtered_calendar_debug.txt")
WINDOW_EVENT_LINES = Path("scripts/debug_windowevent_lines.txt")
REPORT = Path("debug_run_test.txt")

# These target values help identify the specific test-created todo
TARGET_CREATED_AT = "2025-08-01T00:00:00+00:00"
TARGET_TITLE = "WindowEvent Jan 22"


def run_pytest(test_path):
    args = [sys.executable, "-m", "pytest", test_path, "-q", "-s"]
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output = proc.stdout or ""
    lines = output.splitlines()
    return proc.returncode, lines


def write_outputs(return_code, lines):
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with OUT_JSON.open("w", encoding="utf-8") as f:
        json.dump({"return_code": return_code, "lines": lines}, f, indent=2)

    with OUT_TXT.open("w", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")


def extract_windowevent_lines():
    # If pytest produced the filtered calendar debug, read and extract relevant lines
    if not FILTERED_LOG.exists():
        return []
    matches = []
    with FILTERED_LOG.open("r", encoding="utf-8", errors="ignore") as f:
        for ln in f:
            if TARGET_TITLE in ln or 'DEBUG_WINDOWEVENT' in ln or 'calendar_occurrences.todo.inspect' in ln:
                matches.append(ln.rstrip('\n'))

    WINDOW_EVENT_LINES.parent.mkdir(parents=True, exist_ok=True)
    with WINDOW_EVENT_LINES.open("w", encoding="utf-8") as f:
        for ln in matches:
            f.write(ln + "\n")

    return matches


def extract_windowevent_from_pytest_output(py_lines):
    # Sometimes DEBUG_WINDOWEVENT lines appear in the captured pytest output
    matches = []
    for l in py_lines:
        if 'DEBUG_WINDOWEVENT' in l or TARGET_TITLE in l or 'calendar_occurrences.todo.inspect' in l:
            matches.append(l)
    return matches


def find_created_todo_id_from_pytest_output(py_lines):
    # Look for the server log line that indicates the created todo id
    for l in py_lines:
        if 'POST /todos created' in l and TARGET_TITLE in l:
            # example: "POST /todos created WindowEvent todo id=10046 title=WindowEvent Jan 22"
            m = re.search(r"\b(?:todo_id|id)=(\d+)\b", l)
            if m:
                return m.group(1)
    return None


def extract_for_todo_id(window_lines, tid):
    if not tid:
        return []
    filtered = [l for l in window_lines if (f"todo_id={tid}" in l) or (f"id={tid}" in l)]
    # overwrite a separate file
    out = Path(f"scripts/debug_windowevent_{tid}.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for ln in filtered:
            f.write(ln + "\n")
    return filtered


def analyze_and_report(py_lines, window_lines):
    # Simple findings: whether earliest/cap lines exist and whether any cap allows 2027
    findings = []
    findings.append(f"pytest_lines: {len(py_lines)}")
    findings.append(f"window_lines: {len(window_lines)}")

    has_earliest = any('DEBUG_WINDOWEVENT_EARLIEST' in l for l in window_lines)
    has_meta = any('DEBUG_WINDOWEVENT_META' in l for l in window_lines)
    findings.append(f"has_earliest: {has_earliest}")
    findings.append(f"has_meta: {has_meta}")

    # Group window lines by todo id when possible (look for 'todo_id=' or 'id=')
    per_id = {}
    for l in window_lines:
        tid = None
        m = re.search(r"\b(?:todo_id|id)=(\d+)\b", l)
        if m:
            tid = m.group(1)
        key = tid or 'unknown'
        per_id.setdefault(key, []).append(l)

    cap_lines = [l for l in window_lines if 'cap_dt=' in l]
    findings.append(f"cap_lines_count: {len(cap_lines)}")
    cap_allows_2027 = False
    for l in cap_lines:
        idx = l.find('cap_dt=')
        if idx != -1:
            snippet = l[idx + len('cap_dt='):]
            cap_dt = snippet.split()[0]
            if cap_dt.startswith('2027'):
                cap_allows_2027 = True
                findings.append(f"cap_allows_2027_line: {l}")

    findings.append(f"cap_allows_2027: {cap_allows_2027}")

    # Report per-todo-id summaries for the target created_at/title
    findings.append("\nPer-todo-id summaries (first 20 ids):")
    count = 0
    for tid, lines in sorted(per_id.items(), key=lambda x: (x[0] != 'unknown', x[0])):
        if count >= 20:
            break
        findings.append(f"id={tid} line_count={len(lines)}")
        # check for earliest, cap_dt, and any occurrence additions
        found_earliest = any('DEBUG_WINDOWEVENT_EARLIEST' in l for l in lines)
        found_meta = any('DEBUG_WINDOWEVENT_META' in l for l in lines)
        found_added = any('calendar_occurrences.added' in l or "added'" in l or 'calendar_occurrences.added' in l for l in lines)
        findings.append(f"  earliest={found_earliest} meta={found_meta} added={found_added}")
        count += 1

    # Also check pytest output for literal occurrence '2027-01-22' or '2026-01-22'
    occ_2027 = any('2027-01-22' in l for l in py_lines)
    occ_2026 = any('2026-01-22' in l for l in py_lines)
    findings.append(f"occurrence_2027_in_pytest_output: {occ_2027}")
    findings.append(f"occurrence_2026_in_pytest_output: {occ_2026}")

    # Write a short report file (overwrite)
    with REPORT.open("w", encoding="utf-8") as f:
        f.write("Debug run report\n")
        f.write("================\n")
        for ln in findings:
            f.write(ln + "\n")
        f.write("\nSample window lines (first 40):\n")
        for ln in window_lines[:40]:
            f.write(ln + "\n")

    return findings


def detect_truncation(py_lines):
    # Look for 'calendar_occurrences computed X occurrences before user filters (truncated=True)'
    for l in py_lines:
        if 'calendar_occurrences computed' in l and 'truncated=' in l:
            try:
                # extract count and truncated flag
                m = re.search(r'calendar_occurrences computed (\d+) occurrences before user filters \(truncated=(True|False)\)', l)
                if m:
                    return int(m.group(1)), (m.group(2) == 'True')
            except Exception:
                continue
    return None, False


def check_added_for_item(py_lines, tid):
    # Search for calendar_occurrences.added lines that include 'item_id=<tid>'
    needle = f'item_id={tid}'
    for l in py_lines:
        if 'calendar_occurrences.added' in l and needle in l:
            return True
    return False


def main():
    ret, lines = run_pytest(TEST_PATH)
    write_outputs(ret, lines)

    window_lines = extract_windowevent_lines()
    # also pull DEBUG_WINDOWEVENT lines that appeared directly in pytest output
    py_window = extract_windowevent_from_pytest_output(lines)
    if py_window:
        # merge and dedupe while preserving order
        seen = set()
        merged = []
        for l in window_lines + py_window:
            if l not in seen:
                seen.add(l)
                merged.append(l)
        window_lines = merged
    findings = analyze_and_report(lines, window_lines)

    print(f"Wrote {len(lines)} pytest lines to {OUT_JSON} and {OUT_TXT}")
    print(f"Extracted {len(window_lines)} window-event lines to {WINDOW_EVENT_LINES}")
    print("Findings:")
    for ln in findings:
        print(ln)

    # Try to find the created todo id from pytest output and write a per-id file
    tid = find_created_todo_id_from_pytest_output(lines)
    if tid:
        print(f"Found created todo id in pytest output: {tid}")
        per_lines = extract_for_todo_id(window_lines, tid)
        # append per-id findings to the report
        with REPORT.open("a", encoding="utf-8") as f:
            f.write("\nPer-id extraction for created todo id: {}\n".format(tid))
            f.write("File: scripts/debug_windowevent_{}{}.txt\n".format(tid, ""))
            f.write(f"lines_found: {len(per_lines)}\n")
            f.write(f"has_earliest: {any('DEBUG_WINDOWEVENT_EARLIEST' in l for l in per_lines)}\n")
            f.write(f"has_meta: {any('DEBUG_WINDOWEVENT_META' in l for l in per_lines)}\n")
            f.write(f"has_added: {any('calendar_occurrences.added' in l or 'added' in l for l in per_lines)}\n")
            f.write("\nSample per-id lines (first 80):\n")
            for ln in per_lines[:80]:
                f.write(ln + "\n")
    else:
        print("Could not find created todo id in pytest output")

    # detect truncation and whether occurrence for created todo was added
    total, truncated = detect_truncation(lines)
    with REPORT.open("a", encoding="utf-8") as f:
        f.write(f"\ncalendar_occurrences.computed_total: {total}\n")
        f.write(f"calendar_occurrences.truncated: {truncated}\n")
        if tid:
            was_added = check_added_for_item(lines, tid)
            f.write(f"created_todo_occurrence_added: {was_added}\n")
            print(f"created_todo_occurrence_added: {was_added}")


if __name__ == '__main__':
    main()
