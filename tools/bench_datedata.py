"""Benchmark three modes:
 - search_dates with auto-detection
 - search_dates with languages=['en']
 - DateDataParser instance (caches languages)

Writes timings for each mode.
"""
from __future__ import annotations
import time
import argparse

try:
    import dateparser.search
    import dateparser
    # DateDataParser lives in dateparser.date in this version
    from dateparser.date import DateDataParser
except Exception:
    raise SystemExit('dateparser not available')


def bench(file_path: str):
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = [l.rstrip('\n') for l in f]
    n = len(lines)
    print(f'Loaded {n} lines from {file_path}')

    settings = {'RETURN_AS_TIMEZONE_AWARE': True,'TIMEZONE':'UTC','TO_TIMEZONE':'UTC','STRICT_PARSING':True}

    # warm up
    _ = dateparser.search.search_dates(lines[0], settings=settings)

    def run_search(auto: bool, force_en: bool=False):
        start = time.perf_counter()
        for line in lines:
            if auto:
                _ = dateparser.search.search_dates(line, settings=settings)
            elif force_en:
                _ = dateparser.search.search_dates(line, settings=settings, languages=['en'])
        return time.perf_counter()-start

    def run_datedata(seed_langs=None):
        # seed_langs: list[str] or None
        ddp = DateDataParser(languages=seed_langs)
        start = time.perf_counter()
        for line in lines:
            _ = ddp.get_date_data(line)
        return time.perf_counter()-start

    t_auto = run_search(True)
    t_en = run_search(False, True)
    t_ddp = run_datedata(None)

    # seeded languages run will be executed by the caller if provided via globals
    t_seeded = None
    if hasattr(bench, 'seeded_languages') and bench.seeded_languages:
        langs = bench.seeded_languages
        start = time.perf_counter()
        for line in lines:
            _ = dateparser.search.search_dates(line, settings=settings, languages=langs)
        t_seeded = time.perf_counter()-start

    print('Results:')
    print(f'auto: {t_auto:.3f}s')
    print(f'en:   {t_en:.3f}s')
    print(f'DateDataParser: {t_ddp:.3f}s')
    if t_seeded is not None:
        print(f'seeded ({bench.seeded_languages}): {t_seeded:.3f}s')
    # run DateDataParser with seeded languages if provided
    t_ddp_seeded = None
    if hasattr(bench, 'seeded_languages') and bench.seeded_languages:
        t_ddp_seeded = run_datedata(bench.seeded_languages)
        print(f'DateDataParser seeded ({bench.seeded_languages}): {t_ddp_seeded:.3f}s')


if __name__ == '__main__':
    import sys
    p = argparse.ArgumentParser()
    p.add_argument('--file', default='data/date_samples.txt')
    p.add_argument('--languages', type=str, default='')
    args = p.parse_args()
    if args.languages:
        bench.seeded_languages = [s.strip() for s in args.languages.split(',') if s.strip()]
    else:
        bench.seeded_languages = []
    bench(args.file)
