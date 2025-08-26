"""Run a timed benchmark over data/date_samples.txt calling dateparser.search.search_dates
with and without languages=['en'] and report timings.

Usage: python tools/bench_dateparser.py --file data/date_samples.txt
"""
from __future__ import annotations
import time
import argparse
from statistics import mean

try:
    import dateparser.search
    import dateparser
except Exception:
    dateparser = None
    dateparser_search = None
else:
    dateparser_search = dateparser.search


def bench(file_path: str, iterations: int = 1):
    if dateparser is None:
        raise SystemExit('dateparser not available in venv')
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = [l.rstrip('\n') for l in f]
    n = len(lines)
    print(f'Loaded {n} lines from {file_path}')

    def run_once(force_en: bool):
        times = []
        start = time.perf_counter()
        for line in lines:
            t0 = time.perf_counter()
            if force_en:
                res = dateparser.search.search_dates(line, settings={'RETURN_AS_TIMEZONE_AWARE': True,'TIMEZONE':'UTC','TO_TIMEZONE':'UTC','STRICT_PARSING':True}, languages=['en'])
            else:
                res = dateparser.search.search_dates(line, settings={'RETURN_AS_TIMEZONE_AWARE': True,'TIMEZONE':'UTC','TO_TIMEZONE':'UTC','STRICT_PARSING':True})
            t1 = time.perf_counter()
            times.append(t1-t0)
        total = time.perf_counter()-start
        return total, times

    # warmup
    print('Warming up...')
    _ = dateparser.search.search_dates(lines[0], settings={'RETURN_AS_TIMEZONE_AWARE': True,'TIMEZONE':'UTC','TO_TIMEZONE':'UTC','STRICT_PARSING':True}, languages=['en'])

    print('Benchmark: automatic language detection')
    t_auto, times_auto = run_once(False)
    print('Benchmark: force languages=["en"]')
    t_en, times_en = run_once(True)

    print('\nResults:')
    print(f'auto total: {t_auto:.3f}s, per-line avg: {t_auto/len(lines):.6f}s')
    print(f'en total:   {t_en:.3f}s, per-line avg: {t_en/len(lines):.6f}s')
    # show some percentiles
    def pctile(arr, p):
        i = int(len(arr)*p)
        return sorted(arr)[i]
    print('\nPercentiles (per-line seconds):')
    for tag, arr in (('auto', times_auto), ('en', times_en)):
        arr_sorted = sorted(arr)
        print(tag, 'median', pctile(arr_sorted, 0.5), 'p90', pctile(arr_sorted, 0.9), 'p99', pctile(arr_sorted, 0.99))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--file', default='data/date_samples.txt')
    args = p.parse_args()
    bench(args.file)
