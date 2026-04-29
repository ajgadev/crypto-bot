"""Run backtest over a specific year using per-year CSV files.

Usage: python -m scripts.backtest_periods 2025
       python -m scripts.backtest_periods 2026
       python -m scripts.backtest_periods 2025-2026
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from decimal import Decimal

from src.backtest.data_loader import load_csv
from src.backtest.engine import run_backtest
from src.config.settings import Settings

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SYMBOLS = ["BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC"]


def load_period(years: list[str]) -> dict[str, list]:
    by_sym: dict[str, list] = {}
    for sym in SYMBOLS:
        merged: list = []
        for y in years:
            path = os.path.join(DATA_DIR, f"{sym.lower()}_{y}_1h.csv")
            if not os.path.exists(path):
                print(f"  ! missing {path}")
                continue
            ks = load_csv(path)
            merged.extend(ks)
        merged.sort(key=lambda k: k.open_time)
        if merged:
            by_sym[sym] = merged
            print(f"  {sym}: {len(merged)} klines")
    return by_sym


def fmt(d: Decimal) -> str:
    return f"{float(d):+.2f}"


def report(label: str, result, initial: Decimal) -> None:
    print(f"\n{'=' * 70}")
    print(f"BACKTEST: {label}")
    print('=' * 70)

    closed = result.trades
    total_pnl = sum((t.pnl_usdt for t in closed), Decimal("0"))
    wins = sum(1 for t in closed if t.pnl_usdt > 0)
    losses = len(closed) - wins
    final = result.final_equity
    ret_pct = (final / initial - 1) * 100 if initial else Decimal("0")

    print(f"Initial: ${initial}  Final: ${final:.2f}  Return: {ret_pct:+.2f}%")
    print(f"Trades: {len(closed)}  W/L: {wins}/{losses}  "
          f"Win-rate: {(wins/len(closed)*100) if closed else 0:.1f}%")
    print(f"Total PnL: {fmt(total_pnl)}")

    # By strategy
    print("\n--- by strategy ---")
    by_strat: dict[str, list] = defaultdict(list)
    for t in closed:
        by_strat[t.strategy].append(t)
    for strat, trades in sorted(by_strat.items()):
        pnl = sum((t.pnl_usdt for t in trades), Decimal("0"))
        w = sum(1 for t in trades if t.pnl_usdt > 0)
        avg = pnl / Decimal(len(trades)) if trades else Decimal("0")
        print(f"  {strat:15s} n={len(trades):3d}  W/L={w}/{len(trades)-w:<3d}  "
              f"pnl={fmt(pnl):>9s}  avg={fmt(avg):>7s}")

    # By strategy x symbol
    print("\n--- by strategy x symbol ---")
    by_ss: dict[tuple, list] = defaultdict(list)
    for t in closed:
        by_ss[(t.strategy, t.symbol)].append(t)
    for (strat, sym), trades in sorted(by_ss.items()):
        pnl = sum((t.pnl_usdt for t in trades), Decimal("0"))
        w = sum(1 for t in trades if t.pnl_usdt > 0)
        print(f"  {strat:15s} {sym:8s} n={len(trades):3d}  W/L={w}/{len(trades)-w:<3d}  "
              f"pnl={fmt(pnl):>9s}")

    # Exit reasons by strategy
    print("\n--- exit reasons ---")
    by_exit: dict[tuple, list] = defaultdict(list)
    for t in closed:
        by_exit[(t.strategy, t.exit_reason)].append(t)
    for (strat, reason), trades in sorted(by_exit.items()):
        pnl = sum((t.pnl_usdt for t in trades), Decimal("0"))
        print(f"  {strat:15s} {reason:18s} n={len(trades):3d}  pnl={fmt(pnl):>9s}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    spec = sys.argv[1]
    years = spec.split("-") if "-" in spec else [spec]

    print(f"Loading data for years: {years}")
    klines = load_period(years)
    if not klines:
        print("No data loaded.")
        sys.exit(1)

    settings = Settings()
    initial = Decimal("10000")
    result = run_backtest(klines, initial_capital=initial, settings=settings)
    report(f"{spec}  symbols={list(klines.keys())}", result, initial)


if __name__ == "__main__":
    main()
