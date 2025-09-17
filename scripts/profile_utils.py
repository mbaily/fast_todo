#!/usr/bin/env python3
"""CLI utilities for working with cProfile .prof outputs.

Usage examples:
  # Print top cumulative callers
  python scripts/profile_utils.py stats profiles/requests/20250918T120000Z_GET_api_categories_1234.prof --sort cumulative --limit 40

  # Compare two profiles
  python scripts/profile_utils.py diff a.prof b.prof --sort time --limit 30

  # Convert to text for sharing
  python scripts/profile_utils.py dump profiles/global/global_*.prof > profiles/global/last.txt
"""
from __future__ import annotations

import argparse
import glob
import pstats
import sys
from typing import Sequence


def load_stats(paths: Sequence[str]) -> pstats.Stats:
    st: pstats.Stats | None = None
    for p in paths:
        s = pstats.Stats(p)
        if st is None:
            st = s
        else:
            st.add(s)
    if st is None:
        raise SystemExit("no profile files matched")
    return st


def cmd_stats(args: argparse.Namespace) -> int:
    paths = []
    for pat in args.files:
        paths.extend(glob.glob(pat))
    st = load_stats(paths)
    st.strip_dirs().sort_stats(args.sort)
    st.print_stats(args.limit)
    return 0


def cmd_dump(args: argparse.Namespace) -> int:
    return cmd_stats(args)


def cmd_diff(args: argparse.Namespace) -> int:
    # naive diff: print top of each side for manual comparison
    print("=== A ===")
    st_a = load_stats([args.a])
    st_a.strip_dirs().sort_stats(args.sort).print_stats(args.limit)
    print("\n=== B ===")
    st_b = load_stats([args.b])
    st_b.strip_dirs().sort_stats(args.sort).print_stats(args.limit)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="cProfile utilities")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_stats = sub.add_parser("stats", help="print profile stats")
    p_stats.add_argument("files", nargs="+", help=".prof files or globs")
    p_stats.add_argument("--sort", default="cumulative", help="sort key (time|cumulative|calls)")
    p_stats.add_argument("--limit", type=int, default=40, help="number of rows")
    p_stats.set_defaults(func=cmd_stats)

    p_dump = sub.add_parser("dump", help="alias for stats (for piping)")
    p_dump.add_argument("files", nargs="+", help=".prof files or globs")
    p_dump.add_argument("--sort", default="cumulative")
    p_dump.add_argument("--limit", type=int, default=80)
    p_dump.set_defaults(func=cmd_dump)

    p_diff = sub.add_parser("diff", help="compare two profiles")
    p_diff.add_argument("a", help="left profile")
    p_diff.add_argument("b", help="right profile")
    p_diff.add_argument("--sort", default="cumulative")
    p_diff.add_argument("--limit", type=int, default=40)
    p_diff.set_defaults(func=cmd_diff)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
