"""Final performance report with the new optimal config: 2025 vs 2026 separately.

Config:
  TF: ETH, BNB, SOL  (BTC dropped)  | 3% trail-only, no death cross
  MOM: ETH, SOL, BNB                | TP 2.5% / SL 2.2%
  MR:  BTC, ETH, BNB, SOL           | EMA300 trend filter on
  Slots: 2/2/2
"""
from __future__ import annotations

import os
from collections import defaultdict
from decimal import Decimal

from src.backtest.data_loader import load_csv
from src.backtest.engine import run_backtest
from src.config.settings import Settings
from src.strategy import signals as sig_mod
from src.strategy.signals import ExitSignal

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SYMBOLS = ["BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC"]


def load_year(year: str) -> dict[str, list]:
    by: dict[str, list] = {}
    for sym in SYMBOLS:
        p = os.path.join(DATA_DIR, f"{sym.lower()}_{year}_1h.csv")
        if os.path.exists(p):
            by[sym] = load_csv(p)
    return by


def trail_only_exit(trail_pct):
    def fn(entry_price, highest_price, current_price, indicators, settings):
        if current_price <= highest_price * (Decimal("1") - trail_pct):
            return ExitSignal(True, "TRAILING_STOP")
        return ExitSignal(False, "")
    return fn


def run_with_proposed(klines: dict[str, list]):
    settings = Settings().model_copy(update={
        "mean_reversion_symbols": "BTCUSDC,ETHUSDC,BNBUSDC,SOLUSDC",
        "trend_follow_symbols": "ETHUSDC,BNBUSDC,SOLUSDC",
        "momentum_symbols": "ETHUSDC,SOLUSDC,BNBUSDC",
        "trend_follow_trailing_stop_pct": Decimal("0.03"),
        "trend_follow_use_death_cross": False,
    })
    orig = sig_mod.check_trend_follow_exit
    sig_mod.check_trend_follow_exit = trail_only_exit(Decimal("0.03"))
    from src.backtest import engine as eng
    eng.check_trend_follow_exit = sig_mod.check_trend_follow_exit
    try:
        return run_backtest(klines, initial_capital=Decimal("10000"), settings=settings)
    finally:
        sig_mod.check_trend_follow_exit = orig
        eng.check_trend_follow_exit = orig


def report(year: str):
    klines = load_year(year)
    if not klines:
        print(f"\n[no data for {year}]")
        return
    print(f"\n{'#' * 70}")
    print(f"# {year}  — initial $10,000")
    print('#' * 70)
    result = run_with_proposed(klines)
    trades = result.trades
    total_pnl = sum((t.pnl_usdt for t in trades), Decimal("0"))
    wins = sum(1 for t in trades if t.pnl_usdt > 0)
    ret = (result.final_equity / Decimal("10000") - 1) * 100
    print(f"\nTotal: trades={len(trades)}  W/L={wins}/{len(trades)-wins}  "
          f"win-rate={(wins/len(trades)*100 if trades else 0):.1f}%  "
          f"PnL=${float(total_pnl):+.2f}  return={float(ret):+.2f}%")

    by_strat: dict[str, list] = defaultdict(list)
    for t in trades:
        by_strat[t.strategy].append(t)

    print("\n  by strategy:")
    print(f"    {'strategy':<15} {'n':>4} {'W/L':>7} {'win%':>6} {'pnl':>10} {'avg':>8}")
    for strat in sorted(by_strat):
        ts = by_strat[strat]
        p = sum((t.pnl_usdt for t in ts), Decimal("0"))
        w = sum(1 for t in ts if t.pnl_usdt > 0)
        wr = w / len(ts) * 100 if ts else 0
        avg = p / Decimal(len(ts)) if ts else Decimal("0")
        print(f"    {strat:<15} {len(ts):>4} {f'{w}/{len(ts)-w}':>7} "
              f"{wr:>5.0f}% {float(p):>+10.2f} {float(avg):>+8.2f}")

    print("\n  by strategy x coin:")
    print(f"    {'strategy':<15} {'coin':<8} {'n':>3} {'W/L':>6} {'pnl':>10}")
    by_ss: dict[tuple, list] = defaultdict(list)
    for t in trades:
        by_ss[(t.strategy, t.symbol)].append(t)
    for (strat, sym), ts in sorted(by_ss.items()):
        p = sum((t.pnl_usdt for t in ts), Decimal("0"))
        w = sum(1 for t in ts if t.pnl_usdt > 0)
        print(f"    {strat:<15} {sym:<8} {len(ts):>3} {f'{w}/{len(ts)-w}':>6} "
              f"{float(p):>+10.2f}")


def main():
    report("2025")
    report("2026")


if __name__ == "__main__":
    main()
