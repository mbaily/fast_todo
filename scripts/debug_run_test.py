#!/usr/bin/env python3
"""
scripts/debug_run_test.py

Run the single failing pytest test and return the combined stdout/stderr as a list of lines.
The script does not print or write the lines; call `run_failing_test()` to get them.
"""

from __future__ import annotations

import sys
import subprocess
import re
import os
import json
import ast
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime

TEST_NODE = "tests/test_year_resolution_integration.py::test_calendar_window_prefers_window_candidates"


def run_failing_test(py_executable: Optional[str] = None, timeout: int = 120) -> Tuple[List[str], str]:
    """Run the target pytest test and return its output as a list of lines.

    Args:
        py_executable: path to Python executable to run (defaults to sys.executable).
        timeout: seconds to wait for the test run.

    Returns:
        List of output lines (stdout+stderr combined).
    """
    if py_executable is None:
        py_executable = sys.executable

    # Run pytest without -q to get verbose output; keep -s to disable capture
    cmd = [py_executable, "-m", "pytest", TEST_NODE, "-s", "-vv"]

    # Run pytest and capture combined stdout/stderr using Popen.communicate()
    try:
        env = os.environ.copy()
        env.setdefault('PYTHONUNBUFFERED', '1')
        # encourage pytest to show warnings and plugin output
        env.setdefault('PYTHONWARNINGS', 'default')
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        proc_stdout, _ = p.communicate(timeout=timeout)
        proc_returncode = p.returncode
        proc_exc = None
        # If pytest failed early due to warning-config import errors, retry with a minimal pytest.ini
        if proc_stdout and 'while parsing the following warning configuration' in proc_stdout:
            try:
                import tempfile
                tmp = tempfile.NamedTemporaryFile('w', delete=False, suffix='.ini')
                tmp.write('[pytest]\nfilterwarnings = ignore::DeprecationWarning')
                tmp.close()
                cmd2 = list(cmd) + ['-c', tmp.name]
                p2 = subprocess.Popen(cmd2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
                out2, _ = p2.communicate(timeout=timeout)
                proc_stdout = (proc_stdout or '') + '\n--- RETRY OUTPUT ---\n' + (out2 or '')
                proc_returncode = p2.returncode
            except Exception:
                pass

    # If pytest failed due to missing pytest_asyncio, try to install it and rerun once.
    if proc_stdout and ("No module named 'pytest_asyncio'" in proc_stdout or ('ModuleNotFoundError: No module named' in proc_stdout and 'pytest_asyncio' in proc_stdout)):
        try:
            # attempt to install pytest-asyncio
            pip_cmd = [py_executable, '-m', 'pip', 'install', 'pytest-asyncio']
            p_inst = subprocess.run(pip_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300)
            inst_out = p_inst.stdout or ''
            # Rerun pytest once
            p3 = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
            out3, _ = p3.communicate(timeout=timeout)
            proc_stdout = (proc_stdout or '') + '\n--- PIP INSTALL OUTPUT ---\n' + inst_out + '\n--- RERUN OUTPUT ---\n' + (out3 or '')
            proc_returncode = p3.returncode
        except Exception:
            pass
    

    # Persist raw combined output to a timestamped file under /tmp for later inspection.
    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    raw_path = f"/tmp/debug_run_test_raw_{ts}.log"
    meta_path = f"/tmp/debug_run_test_meta_{ts}.json"
    try:
        # always create the raw file (may be empty)
        with open(raw_path, 'w') as rf:
            rf.write(proc_stdout or '')
        meta = {'returncode': proc_returncode, 'cmd': cmd, 'ts': ts, 'exc': proc_exc}
        with open(meta_path, 'w') as mf:
            json.dump(meta, mf)
    except Exception:
        # best-effort: ignore failures to write files; still return what we have
        raw_path = raw_path if raw_path else ''

    # Print a small status JSON line for interactive runs so caller sees where raw_path/meta are.
    try:
        status = {'raw_path': raw_path, 'meta_path': meta_path, 'returncode': proc_returncode}
        print(json.dumps(status), flush=True)
    except Exception:
        pass

    # Return output as list of lines and the raw_path where it was saved.
    # If subprocess produced no output, try running pytest in-process to capture logs.
    if not proc_stdout:
        try:
            import io
            import contextlib
            import pytest

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                try:
                    # run pytest in-process
                    rv = pytest.main(["-q", TEST_NODE, "-s"])
                except Exception as e:
                    buf.write(repr(e))
            proc_stdout = buf.getvalue()
            # rewrite raw file and meta with new content
            try:
                with open(raw_path, 'w') as rf:
                    rf.write(proc_stdout or '')
                meta = {'returncode': rv if 'rv' in locals() else None, 'cmd': ['pytest.main', TEST_NODE], 'ts': ts, 'exc': proc_exc}
                with open(meta_path, 'w') as mf:
                    json.dump(meta, mf)
            except Exception:
                pass
        except Exception:
            # if pytest not available or in-process run fails, continue with existing proc_stdout
            pass

    return (proc_stdout.splitlines() if proc_stdout is not None else [], raw_path)


def analyze_log_lines(lines: list[str]) -> str:
    """Analyze pytest run log lines and extract relevant calendar_occurrences info.

    Prints a concise summary and returns a JSON string with details.
    """
    # Find todo inspect lines that reference 'WindowEvent Jan 22'
    window_todos: Dict[int, Dict[str, Any]] = {}

    inspect_re = re.compile(r'id=(\d+)')
    created_re = re.compile(r'created_at=([0-9T:\-+:.]+)')

    for idx, line in enumerate(lines):
        if 'calendar_occurrences.todo.inspect' in line and 'WindowEvent' in line:
            # Try to extract id
            m = inspect_re.search(line)
            todo_id = int(m.group(1)) if m else None
            created_at = None
            if todo_id is None:
                continue

            # Try to find created_at on same line or following few lines
            m2 = created_re.search(line)
            if m2:
                created_at = m2.group(1)
            else:
                # look ahead up to 2 lines
                for j in range(1, 3):
                    if idx + j < len(lines):
                        m3 = created_re.search(lines[idx + j])
                        if m3:
                            created_at = m3.group(1)
                            break

            window_todos[todo_id] = {
                'id': todo_id,
                'inspect_line': line.strip(),
                'created_at': created_at,
                'added': [],
                'filtered_out': [],
                'meta': [],
                'yearless_match': [],
                'earliest_candidate': [],
            }

    # If none found, bail with empty summary
    if not window_todos:
        summary = {
            'test_node': TEST_NODE,
            'window_event_todos': [],
            'summary': {'num_window_todos': 0}
        }
        print('No WindowEvent todos found in logs.')
        return json.dumps(summary)

    # Compile patterns to search for events related to these ids
    ids_str = '|'.join(str(i) for i in window_todos.keys())
    added_re = re.compile(r'calendar_occurrences\.added.*item_type=todo.*item_id=(\d+).*occurrence=([^\s]+)')
    filtered_re = re.compile(r'calendar_occurrences\.filtered_out.*item_type=todo.*item_id=(\d+).*reason=([^\s]+)')
    meta_re = re.compile(r'calendar_occurrences\.todo\.meta.*id=(\d+).*')
    yearless_re = re.compile(r'calendar_occurrences\.todo\.yearless_match.*id=(\d+).*candidate=([^\s]+)')
    earliest_re = re.compile(r'calendar_occurrences\.todo\.earliest_candidate.*id=(\d+).*candidate=([^\s]+)')

    # Scan lines once and attach matches
    for line in lines:
        m = added_re.search(line)
        if m:
            tid = int(m.group(1))
            occ = m.group(2)
            if tid in window_todos:
                window_todos[tid]['added'].append({'occurrence': occ, 'line': line.strip()})
        m = filtered_re.search(line)
        if m:
            tid = int(m.group(1))
            reason = m.group(2)
            if tid in window_todos:
                window_todos[tid]['filtered_out'].append({'reason': reason, 'line': line.strip()})
        m = yearless_re.search(line)
        if m:
            tid = int(m.group(1))
            cand = m.group(2)
            if tid in window_todos:
                window_todos[tid]['yearless_match'].append({'candidate': cand, 'line': line.strip()})
        m = earliest_re.search(line)
        if m:
            tid = int(m.group(1))
            cand = m.group(2)
            if tid in window_todos:
                window_todos[tid]['earliest_candidate'].append({'candidate': cand, 'line': line.strip()})
        m = meta_re.search(line)
        if m:
            tid = int(m.group(1))
            if tid in window_todos:
                window_todos[tid]['meta'].append({'line': line.strip()})

    # Build summary object
    todo_list = []
    for tid, info in sorted(window_todos.items()):
        todo_summary = {
            'id': tid,
            'created_at': info['created_at'],
            'num_added': len(info['added']),
            'num_filtered_out': len(info['filtered_out']),
            'added': info['added'][:10],
            'filtered_out': info['filtered_out'][:10],
            'yearless_match': info['yearless_match'][:10],
            'earliest_candidate': info['earliest_candidate'][:10],
            'meta': info['meta'][:5],
        }
        todo_list.append(todo_summary)

    summary = {
        'test_node': TEST_NODE,
        'window_event_todos': todo_list,
        'summary': {
            'num_window_todos': len(todo_list),
            'ids': [t['id'] for t in todo_list],
            'total_added_events': sum(t['num_added'] for t in todo_list),
            'total_filtered_out_events': sum(t['num_filtered_out'] for t in todo_list),
        }
    }

    # Print concise info for quick debugging
    print(f"Found {summary['summary']['num_window_todos']} WindowEvent todos: {summary['summary']['ids']}")
    for t in summary['window_event_todos']:
        print(f"- todo id={t['id']} created_at={t['created_at']} added={t['num_added']} filtered_out={t['num_filtered_out']}")
        if t['added']:
            print(f"  first added occurrence: {t['added'][0]['occurrence']}")
        if t['filtered_out']:
            print(f"  first filtered reason: {t['filtered_out'][0]['reason']}")

    return json.dumps(summary)


def extract_todo_trace(lines: list[str], todo_id: int) -> str:
    """Return a JSON string with a chronological trace of relevant log lines for todo_id.

    The returned JSON has keys:
      - id: the todo id
      - trace: list of {'time','level','msg','raw'} entries in original order
    """
    trace: list[dict] = []
    # look for item_id=<todo_id> and our custom 'todo id=<id>' inspect logs
    id_token = f"item_id={todo_id}"
    inspect_token = f"todo id={todo_id}"
    for raw in lines:
        if id_token in raw or inspect_token in raw:
            parts = raw.split(None, 3)
            entry: dict = {"raw": raw}
            if len(parts) >= 1:
                entry["time"] = parts[0]
            if len(parts) >= 3:
                entry["level"] = parts[2].rstrip(":")
            entry["msg"] = parts[3] if len(parts) >= 4 else ""
            trace.append(entry)

    out = {"id": todo_id, "trace": trace}
    return json.dumps(out, indent=2)


def find_created_todo_id(lines: list[str], title_substr: str) -> Optional[int]:
    """Search test run logs for the POST /todos creation logger lines and return the todo id.

    The server logs include lines like: "POST /todos created WindowEvent todo id=<id> title=<text>"
    This function returns the first id where the title contains title_substr.
    """
    cre_re = re.compile(r'POST /todos created .* id=(\d+) title=(.*)')
    for line in lines:
        m = cre_re.search(line)
        if m:
            tid = int(m.group(1))
            title = m.group(2)
            if title_substr in title:
                return tid
    return None


def _cli_find_and_trace(title_substr: str) -> int:
    """Run the failing test, find the created todo with title_substr, print its trace JSON.

    Returns exit code 0 on success, 2 if not found.
    """
    lines, raw = run_failing_test()
    tid = find_created_todo_id(lines, title_substr)
    if not tid:
        print(json.dumps({"error": "created todo not found", "title_substr": title_substr}))
        return 2
    trace_json = extract_todo_trace(lines, tid)
    # Include raw path if available
    try:
        trace_obj = json.loads(trace_json)
    except Exception:
        trace_obj = {'trace_json': trace_json}
    if raw:
        trace_obj['raw_path'] = raw
    print(json.dumps(trace_obj, indent=2))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description='Debug helper for failing year-resolution test')
    sub = p.add_subparsers(dest='cmd')

    fa = sub.add_parser('find-and-trace')
    fa.add_argument('title', help='Title substring to match creation log (e.g. "WindowEvent Jan 22")')

    ta = sub.add_parser('trace')
    ta.add_argument('id', type=int, help='Todo id to trace from last test run')
    tc = sub.add_parser('collect')
    tc.add_argument('id', type=int, help='Todo id to collect calendar events for')
    tc.add_argument('--out', help='Path to write JSON output (defaults to /tmp/debug_collect_<id>.json)')

    dd = sub.add_parser('dump')
    dd.add_argument('id', type=int, help='Todo id to dump calendar_occurrences lines for')
    dd.add_argument('--out', help='Path to write JSON output (optional)')

    aa = sub.add_parser('auto')
    aa.add_argument('--title', help='Title substring to find created todo', default='WindowEvent Jan 22')
    aa.add_argument('--out', help='Path to write JSON output (optional)')

    # parse-raw: parse a previously saved raw pytest log without running pytest
    pr = sub.add_parser('parse-raw')
    pr.add_argument('raw_path', help='Path to raw log file to parse')
    pr.add_argument('id', type=int, help='Todo id to collect events for')
    pr.add_argument('--out', help='Optional path to write parsed JSON')

    args = p.parse_args(argv)

    # Write a start marker so we can confirm the script executed under the test harness.
    try:
        start_marker = {'argv': sys.argv, 'cwd': os.getcwd()}
        with open('/tmp/debug_run_test_start.json', 'w') as sm:
            json.dump(start_marker, sm)
    except Exception:
        pass

    if args.cmd == 'find-and-trace':
        return _cli_find_and_trace(args.title)

    if args.cmd == 'parse-raw':
        # Read the raw file and parse events for given todo id
        try:
            with open(args.raw_path, 'r') as rf:
                raw_text = rf.read()
        except Exception as e:
            print(json.dumps({'error': 'failed to read raw_path', 'exc': str(e)}))
            return 2
        lines = raw_text.splitlines()
        out_obj = collect_calendar_events_for_todo(lines, args.id)

        # Basic raw-line scans to help locate instrumentation even when payload parsing fails
        total_lines = len(lines)
        cal_lines = [l for l in lines if 'calendar_occurrences' in l]
        window_lines = [l for l in lines if 'WindowEvent' in l]
        # match exact tokens for id: ' id=10017', 'item_id=10017', '(10017,'
        id_token_patterns = [f' id={args.id}', f'item_id={args.id}', f'({args.id},', f'[{args.id},']
        id_lines = [l for l in lines if any(tok in l for tok in id_token_patterns)]

        out_obj['_raw_summary'] = {
            'total_lines': total_lines,
            'calendar_occurrences_lines': len(cal_lines),
            'windowevent_lines': len(window_lines),
            'id_token_lines': len(id_lines),
            'calendar_occurrences_sample': cal_lines[:200],
            'windowevent_sample': window_lines[:200],
            'id_lines_sample': id_lines[:200],
        }

        # Extract traceback blocks and ImportError/ModuleNotFoundError messages for diagnostics
        errors = []
        tb_lines = []
        in_tb = False
        for ln in lines:
            if ln.startswith('Traceback'):
                in_tb = True
                tb_lines = [ln]
                continue
            if in_tb:
                if ln.strip() == '':
                    errors.append('\n'.join(tb_lines))
                    in_tb = False
                    tb_lines = []
                else:
                    tb_lines.append(ln)
            else:
                if 'ImportError' in ln or 'ModuleNotFoundError' in ln or 'ERROR:' in ln:
                    errors.append(ln)

        if in_tb and tb_lines:
            errors.append('\n'.join(tb_lines))

        out_obj['_raw_summary']['errors'] = errors[:20]
        if hasattr(args, 'out') and args.out:
            try:
                with open(args.out, 'w') as f:
                    json.dump(out_obj, f, indent=2)
                print(json.dumps({'wrote': args.out}))
            except Exception as e:
                print(json.dumps({'error': 'failed to write out', 'exc': str(e)}))
                return 2
        else:
            print(json.dumps(out_obj, indent=2))
        return 0

    if args.cmd == 'trace':
        lines, raw = run_failing_test()
        out = json.loads(extract_todo_trace(lines, args.id))
        if raw:
            out['raw_path'] = raw
        print(json.dumps(out, indent=2))
        return 0

    if args.cmd == 'collect':
        lines, raw = run_failing_test()
        out_obj = collect_calendar_events_for_todo(lines, args.id)
        if raw:
            out_obj['raw_path'] = raw
        # choose output path
        out_path = args.out if hasattr(args, 'out') and args.out else f"/tmp/debug_collect_{args.id}.json"
        try:
            with open(out_path, 'w') as f:
                json.dump(out_obj, f)
        except Exception as e:
            print(json.dumps({'error': 'failed to write output', 'exc': str(e)}))
            return 2

        # print small pointer summary
        short = {
            'id': out_obj.get('id'),
            'num_added': len(out_obj.get('added', [])),
            'num_filtered_out': len(out_obj.get('filtered_out', [])),
            'num_yearless_match': len(out_obj.get('yearless_match', [])),
            'path': out_path,
        }
        print(json.dumps(short))
        return 0

    if args.cmd == 'dump':
        lines, raw = run_failing_test()
        out_obj = collect_calendar_events_for_todo(lines, args.id)
        if raw:
            out_obj['raw_path'] = raw
        # Print chronological events for quick inspection
        print(f"Dump for todo id={args.id}: events={len(out_obj.get('events', []))} added={len(out_obj.get('added', []))} earliest_candidate={len(out_obj.get('earliest_candidate', []))} yearless_match={len(out_obj.get('yearless_match', []))} filtered_out={len(out_obj.get('filtered_out', []))}")
        for e in out_obj.get('events', []):
            # Print concise line: event name and payload keys
            payload = e.get('payload')
            if isinstance(payload, dict):
                keys = ','.join(sorted(payload.keys()))
            else:
                keys = str(type(payload))
            print(f"- {e.get('event')} payload_keys=[{keys}] raw={e.get('raw')}")

        # Also print structured added/earliest/yearless entries
        if out_obj.get('added'):
            print('\nAdded occurrences:')
            for a in out_obj['added']:
                print('  -', a)
        if out_obj.get('earliest_candidate'):
            print('\nEarliest candidates:')
            for a in out_obj['earliest_candidate']:
                print('  -', a)
        if out_obj.get('yearless_match'):
            print('\nYearless matches:')
            for a in out_obj['yearless_match']:
                print('  -', a)

        # Optionally write JSON output
        if hasattr(args, 'out') and args.out:
            try:
                with open(args.out, 'w') as f:
                    json.dump(out_obj, f, indent=2)
                print(f"Wrote dump to {args.out}")
            except Exception as e:
                print(json.dumps({'error': 'failed to write dump', 'exc': str(e)}))
                return 2
        return 0

    if args.cmd == 'auto':
        lines, raw = run_failing_test()
        tid = find_created_todo_id(lines, args.title)
        if not tid:
            print(json.dumps({'error': 'created todo not found', 'title': args.title}))
            return 2
        out_obj = collect_calendar_events_for_todo(lines, tid)
        if raw:
            out_obj['raw_path'] = raw
        print(f"Auto-collect for title={args.title} -> todo id={tid}: events={len(out_obj.get('events', []))} added={len(out_obj.get('added', []))} earliest_candidate={len(out_obj.get('earliest_candidate', []))} yearless_match={len(out_obj.get('yearless_match', []))} filtered_out={len(out_obj.get('filtered_out', []))} raw_path={raw}")
        # print first few structured events
        for e in out_obj.get('events', [])[:50]:
            payload = e.get('payload')
            if isinstance(payload, dict):
                keys = ','.join(sorted(payload.keys()))
            else:
                keys = str(type(payload))
            print(f"- {e.get('event')} payload_keys=[{keys}]")
        if hasattr(args, 'out') and args.out:
            try:
                with open(args.out, 'w') as f:
                    json.dump(out_obj, f, indent=2)
                print(f"Wrote auto-collect to {args.out}")
            except Exception as e:
                print(json.dumps({'error': 'failed to write auto output', 'exc': str(e)}))
                return 2
        return 0

    # default: show help
    p.print_help()
    return 1



def collect_calendar_events_for_todo(lines: list[str], todo_id: int) -> dict:
    """Collect calendar-related events for a specific todo id from the test run logs.

    Returns a dict with lists: 'added', 'filtered_out', 'yearless_match', 'earliest_candidate', 'inspect', 'meta'.
    """
    out = {
        'id': todo_id,
        'events': [],  # chronological raw events matching calendar_occurrences
        'added': [],
        'filtered_out': [],
        'yearless_match': [],
        'earliest_candidate': [],
        'inspect': [],
        'meta': [],
    }

    # Generic pattern: event name followed by a python dict literal (as logged)
    event_re = re.compile(r'calendar_occurrences\.([A-Za-z0-9_.]+)\s+(\{.*\})')

    for line in lines:
        m = event_re.search(line)
        if not m:
            # not an instrumentation line we care about
            continue

        event_name = m.group(1)
        payload_str = m.group(2)
        payload = None
        try:
            # logs use Python dict repr (single quotes); ast.literal_eval handles it
            payload = ast.literal_eval(payload_str)
        except Exception:
            # fallback: try JSON
            try:
                payload = json.loads(payload_str)
            except Exception:
                # unable to parse payload; store raw line and continue
                out['events'].append({'event': event_name, 'raw': line.strip(), 'payload': None})
                continue

        out['events'].append({'event': event_name, 'raw': line.strip(), 'payload': payload})

        # Normalize detection of todo id across payload shapes
        pid = None
        if isinstance(payload, dict):
            pid = payload.get('item_id') or payload.get('todo_id') or payload.get('id')

        # Helper: check whether this payload is about our todo
        is_todo = (pid == todo_id)
        # Some payloads contain large lists (fetched_todos) where the todo id appears inside
        if not is_todo and isinstance(payload, (list, tuple)):
            try:
                # search for tuples/lists whose first element equals todo_id
                for it in payload:
                    if isinstance(it, (list, tuple)) and len(it) > 0 and it[0] == todo_id:
                        is_todo = True
                        break
            except Exception:
                pass

        # Record structured events if they match our todo
        try:
            if event_name.endswith('added') or event_name == 'added':
                if is_todo:
                    # payload may be a dict with occurrence or fields
                    occ = payload.get('occurrence') or payload.get('occurrence_iso') or payload.get('dt') or payload.get('occurrence')
                    out['added'].append({'occurrence': occ, 'line': line.strip(), 'payload': payload})
            elif 'filtered_out' in event_name or event_name == 'filtered_out':
                if is_todo:
                    out['filtered_out'].append({'reason': payload.get('reason'), 'line': line.strip(), 'payload': payload})
            elif 'yearless_match' in event_name:
                if is_todo:
                    out['yearless_match'].append({'candidate': payload.get('candidate') or payload.get('month'), 'line': line.strip(), 'payload': payload})
            elif 'earliest_candidate' in event_name:
                if is_todo:
                    out['earliest_candidate'].append({'candidate': payload.get('earliest') or payload.get('candidate'), 'line': line.strip(), 'payload': payload})
            elif 'branch_choice' in event_name:
                if is_todo:
                    out.setdefault('branch_choice', []).append({'choice': payload.get('chosen_branch') or payload.get('choice'), 'line': line.strip(), 'payload': payload})
            elif 'truncated' in event_name:
                if is_todo:
                    out.setdefault('truncated', []).append({'reason': payload.get('reason') or payload.get('when'), 'line': line.strip(), 'payload': payload})
            elif 'candidate_considered' in event_name:
                if is_todo:
                    out.setdefault('candidate_considered', []).append({'candidate': payload, 'line': line.strip()})
            elif event_name.startswith('todo.') and isinstance(payload, dict):
                # todo.* events include todo_id in payload
                if payload.get('todo_id') == todo_id:
                    if event_name.endswith('meta'):
                        out['meta'].append({'line': line.strip(), 'payload': payload})
                    if event_name.endswith('inspect'):
                        out['inspect'].append({'line': line.strip(), 'payload': payload})
        except Exception:
            # best-effort: ignore parse errors
            pass

    return out


if __name__ == '__main__':
    raise SystemExit(main())
