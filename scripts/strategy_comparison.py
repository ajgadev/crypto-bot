"""Print pre/post strategy-change comparison from a local DB.

Usage:
    .venv/bin/python -m scripts.strategy_comparison
    .venv/bin/python -m scripts.strategy_comparison --db db/trading_bot.db --cutoff 2026-04-29
"""
from __future__ import annotations

import argparse

from src.reports.strategy_comparison import build_comparison, format_telegram


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="db/trading_bot.db")
    p.add_argument("--cutoff", default="2026-04-29",
                   help="ISO date; trades entered before this go in 'pre'.")
    args = p.parse_args()

    cutoff_iso = args.cutoff if "T" in args.cutoff else f"{args.cutoff}T00:00:00+00:00"
    comparison = build_comparison(args.db, cutoff_iso)
    print(format_telegram(comparison, cutoff_iso))


if __name__ == "__main__":
    main()
