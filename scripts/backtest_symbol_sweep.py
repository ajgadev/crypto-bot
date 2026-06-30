"""Test adding symbols to strategies.

Window: 2025-01-01 → 2026-02-28 (limited by full-CSV coverage of new symbols).
Uses the new TF config (3% trail-only, no death cross).
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from src.backtest.data_loader import load_csv
from src.backtest.engine import run_backtest
from src.config.settings import Settings
from src.strategy import signals as sig_mod
from src.strategy.signals import ExitSignal

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
START_MS = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = int(datetime(2026, 2, 28, tzinfo=timezone.utc).timestamp() * 1000)

# Core 4 symbols loaded from per-year files
CORE = ["BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC"]
# Candidate additions loaded from full CSVs
CANDIDATES = ["ADAUSDC", "AVAXUSDC", "LINKUSDC", "XRPUSDC", "DOGEUSDC", "DOTUSDC"]


def load_klines() -> dict[str, list]:
    by: dict[str, list] = {}
    for sym in CORE:
        merged: list = []
        for y in ["2025", "2026"]:
            p = os.path.join(DATA_DIR, f"{sym.lower()}_{y}_1h.csv")
            if os.path.exists(p):
                merged.extend(load_csv(p))
        merged = [k for k in merged if START_MS <= k.open_time <= END_MS]
        merged.sort(key=lambda k: k.open_time)
        if merged:
            by[sym] = merged
    for sym in CANDIDATES:
        p = os.path.join(DATA_DIR, f"{sym.lower()}_full_1h.csv")
        if not os.path.exists(p):
            continue
        ks = [k for k in load_csv(p) if START_MS <= k.open_time <= END_MS]
        ks.sort(key=lambda k: k.open_time)
        if ks:
            by[sym] = ks
    return by


def trail_only_exit(trail_pct: Decimal):
    def fn(entry_price, highest_price, current_price, indicators, settings):
        floor = highest_price * (Decimal("1") - trail_pct)
        if current_price <= floor:
            return ExitSignal(True, "TRAILING_STOP")
        return ExitSignal(False, "")
    return fn


def run_variant(label: str, klines, *, mr_syms, tf_syms, mom_syms) -> dict:
    settings = Settings().model_copy(update={
        "mean_reversion_symbols": ",".join(mr_syms),
        "trend_follow_symbols": ",".join(tf_syms),
        "momentum_symbols": ",".join(mom_syms),
        "trend_follow_trailing_stop_pct": Decimal("0.03"),
        "trend_follow_use_death_cross": False,
    })
    # Only feed engine the symbols we care about (faster + cleaner)
    needed = set(mr_syms) | set(tf_syms) | set(mom_syms)
    sub = {s: klines[s] for s in needed if s in klines}

    # Patch TF exit to honor the trail_only setting (engine reads from signals module)
    original = sig_mod.check_trend_follow_exit
    sig_mod.check_trend_follow_exit = trail_only_exit(Decimal("0.03"))
    from src.backtest import engine as eng
    eng.check_trend_follow_exit = sig_mod.check_trend_follow_exit
    try:
        result = run_backtest(sub, initial_capital=Decimal("10000"), settings=settings)
    finally:
        sig_mod.check_trend_follow_exit = original
        eng.check_trend_follow_exit = original

    by_strat: dict[str, list] = defaultdict(list)
    for t in result.trades:
        by_strat[t.strategy].append(t)

    total_pnl = sum((t.pnl_usdt for t in result.trades), Decimal("0"))
    ret = (result.final_equity / Decimal("10000") - 1) * 100
    print(f"\n>>> {label}")
    print(f"    total: trades={len(result.trades)}  pnl={float(total_pnl):+.2f}  "
          f"return={float(ret):+.2f}%")
    summary = {}
    for strat in sorted(by_strat):
        ts = by_strat[strat]
        pnl = sum((t.pnl_usdt for t in ts), Decimal("0"))
        w = sum(1 for t in ts if t.pnl_usdt > 0)
        summary[strat] = (len(ts), w, pnl)
        print(f"      {strat:15s} n={len(ts):3d}  W/L={w}/{len(ts)-w:<3d}  "
              f"pnl={float(pnl):+8.2f}")

    by_ss: dict[tuple, list] = defaultdict(list)
    for t in result.trades:
        by_ss[(t.strategy, t.symbol)].append(t)
    for (strat, sym), ts in sorted(by_ss.items()):
        pnl = sum((t.pnl_usdt for t in ts), Decimal("0"))
        w = sum(1 for t in ts if t.pnl_usdt > 0)
        print(f"        {strat:15s} {sym:8s} n={len(ts):3d} W/L={w}/{len(ts)-w:<3d} "
              f"pnl={float(pnl):+8.2f}")
    return summary


def main() -> None:
    print("Loading 2025-01 → 2026-02 data...")
    klines = load_klines()
    for s, ks in klines.items():
        print(f"  {s}: {len(ks)} klines")

    base_mr = ["BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC"]
    base_tf = ["ETHUSDC", "BNBUSDC", "SOLUSDC"]
    base_mom = ["ETHUSDC", "SOLUSDC", "BNBUSDC"]

    print("\n" + "=" * 70)
    print("BASELINE — current proposed config")
    print("=" * 70)
    run_variant("baseline", klines,
                mr_syms=base_mr, tf_syms=base_tf, mom_syms=base_mom)

    print("\n" + "=" * 70)
    print("MOM — add BTCUSDC")
    print("=" * 70)
    run_variant("MOM + BTC", klines,
                mr_syms=base_mr, tf_syms=base_tf,
                mom_syms=base_mom + ["BTCUSDC"])

    print("\n" + "=" * 70)
    print("Add candidate symbols ONE AT A TIME to all strategies")
    print("=" * 70)
    for cand in CANDIDATES:
        if cand not in klines:
            continue
        run_variant(f"all strats + {cand}", klines,
                    mr_syms=base_mr + [cand],
                    tf_syms=base_tf + [cand],
                    mom_syms=base_mom + [cand])

    print("\n" + "=" * 70)
    print("Add candidate symbols to MOM ONLY (highest-EV strategy)")
    print("=" * 70)
    for cand in CANDIDATES:
        if cand not in klines:
            continue
        run_variant(f"MOM + {cand}", klines,
                    mr_syms=base_mr, tf_syms=base_tf,
                    mom_syms=base_mom + [cand])


if __name__ == "__main__":
    main()
