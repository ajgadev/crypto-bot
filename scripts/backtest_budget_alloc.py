"""Test FCFS vs budget allocation, with $1050 capital matching current Binance balance.

Runs both 2025 and 2026 separately with the proposed config (TF=ETH/BNB/SOL, 3% trail).
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
INITIAL = Decimal("1050")


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


def run(label: str, year: str, *, alloc_enabled: bool,
        mr_pct=Decimal("0.20"), tf_pct=Decimal("0.20"), mom_pct=Decimal("0.60")):
    klines = load_year(year)
    settings = Settings().model_copy(update={
        "mean_reversion_symbols": "BTCUSDC,ETHUSDC,BNBUSDC,SOLUSDC",
        "trend_follow_symbols": "ETHUSDC,BNBUSDC,SOLUSDC",
        "momentum_symbols": "ETHUSDC,SOLUSDC,BNBUSDC",
        "trend_follow_trailing_stop_pct": Decimal("0.03"),
        "trend_follow_use_death_cross": False,
        "budget_allocation_enabled": alloc_enabled,
        "mr_budget_pct": mr_pct,
        "tf_budget_pct": tf_pct,
        "mom_budget_pct": mom_pct,
    })
    orig = sig_mod.check_trend_follow_exit
    sig_mod.check_trend_follow_exit = trail_only_exit(Decimal("0.03"))
    from src.backtest import engine as eng
    eng.check_trend_follow_exit = sig_mod.check_trend_follow_exit
    try:
        result = run_backtest(klines, initial_capital=INITIAL, settings=settings)
    finally:
        sig_mod.check_trend_follow_exit = orig
        eng.check_trend_follow_exit = orig

    trades = result.trades
    pnl = sum((t.pnl_usdt for t in trades), Decimal("0"))
    wins = sum(1 for t in trades if t.pnl_usdt > 0)
    ret = (result.final_equity / INITIAL - 1) * 100
    print(f"\n>>> {year} | {label}")
    print(f"    trades={len(trades)}  W/L={wins}/{len(trades)-wins}  "
          f"PnL=${float(pnl):+.2f}  return={float(ret):+.2f}%  "
          f"final=${float(result.final_equity):.2f}")

    by_strat: dict[str, list] = defaultdict(list)
    for t in trades:
        by_strat[t.strategy].append(t)
    for strat in sorted(by_strat):
        ts = by_strat[strat]
        p = sum((t.pnl_usdt for t in ts), Decimal("0"))
        w = sum(1 for t in ts if t.pnl_usdt > 0)
        print(f"      {strat:15s} n={len(ts):3d}  W/L={w}/{len(ts)-w:<3d}  "
              f"pnl={float(p):+8.2f}")
    return float(pnl), len(trades)


def main():
    print(f"Initial capital: ${INITIAL}")
    print("Comparing FCFS (current) vs budget allocation 20/20/60 (env defaults)")
    print("Also testing 25/35/40 (heavier on best strategies)\n")

    for year in ["2025", "2026"]:
        print("=" * 70)
        run("FCFS (alloc disabled)", year, alloc_enabled=False)
        run("Alloc 20/20/60 (MR/TF/MOM)", year, alloc_enabled=True,
            mr_pct=Decimal("0.20"), tf_pct=Decimal("0.20"), mom_pct=Decimal("0.60"))
        run("Alloc 25/35/40", year, alloc_enabled=True,
            mr_pct=Decimal("0.25"), tf_pct=Decimal("0.35"), mom_pct=Decimal("0.40"))
        run("Alloc 33/33/34 (equal)", year, alloc_enabled=True,
            mr_pct=Decimal("0.33"), tf_pct=Decimal("0.33"), mom_pct=Decimal("0.34"))


if __name__ == "__main__":
    main()
