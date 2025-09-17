"""Extract SUBLETS_DUMP JSON payloads from a server log and emit JSON-lines.

Reads from a logfile path or from stdin. Prints one JSON object per dump:
  {"list": <id>, "data": [...]}

Options:
  --log PATH   path to server.log (default stdin)
  --list-id N  only emit dumps for that list id

Example:
  python3 scripts/parse_server_sublists.py --log server.log --list-id 190
  cat server.log | python3 scripts/parse_server_sublists.py
"""

import sys
import re
import json
import argparse


def iter_lines_from(path):
    if not path:
        for ln in sys.stdin:
            yield ln
        return
    with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
        for ln in fh:
            yield ln


def parse_stream(lines, list_id_filter=None):
    # non-greedy to avoid huge capture across lines; allow whitespace
    start_re = re.compile(r'SUBLETS_DUMP_START\s+list=(\d+)\s+data=(\[.*?\])\s+SUBLETS_DUMP_END')
    for ln in lines:
        m = start_re.search(ln)
        if not m:
            continue
        lid = int(m.group(1))
        if list_id_filter is not None and lid != list_id_filter:
            continue
        data = m.group(2)
        try:
            payload = json.loads(data)
        except Exception:
            yield {'list': lid, 'data_raw': data}
            continue
        yield {'list': lid, 'data': payload}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--log', help='Path to server.log (default stdin)')
    p.add_argument('--list-id', type=int, help='Filter by list id')
    args = p.parse_args()

    for obj in parse_stream(iter_lines_from(args.log), list_id_filter=args.list_id):
        print(json.dumps(obj, ensure_ascii=False))


if __name__ == '__main__':
    main()
