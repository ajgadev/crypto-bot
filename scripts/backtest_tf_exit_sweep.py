"""Sweep TF exit configurations to see if a tighter trailing stop fixes TF.

Tests against current TREND_FOLLOW_SYMBOLS (BTC, ETH, BNB, SOL) on 2025-2026.
Production code is NOT modified — exit fn is monkey-patched per run.

Usage: .venv/bin/python -m scripts.backtest_tf_exit_sweep
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
YEARS = ["2025", "2026"]


def load_data() -> dict[str, list]:
    by_sym: dict[str, list] = {}
    for sym in SYMBOLS:
        merged: list = []
        for y in YEARS:
            path = os.path.join(DATA_DIR, f"{sym.lower()}_{y}_1h.csv")
            if os.path.exists(path):
                merged.extend(load_csv(path))
        merged.sort(key=lambda k: k.open_time)
        if merged:
            by_sym[sym] = merged
    return by_sym


def make_tf_exit_fn(trail_pct: Decimal | None, use_death_cross: bool):
    """Build a custom TF-exit function with the given config."""

    def fn(entry_price, highest_price, current_price, indicators, settings):
        if trail_pct is not None:
            trail_floor = highest_price * (Decimal("1") - trail_pct)
            if current_price <= trail_floor:
                return ExitSignal(True, "TRAILING_STOP")
        if use_death_cross and indicators.ema_short < indicators.ema_long:
            return ExitSignal(True, "DEATH_CROSS")
        return ExitSignal(False, "")

    return fn


def run_variant(label: str, klines, *, trail_pct, use_death_cross,
                tf_symbols: list[str] | None = None) -> None:
    settings = Settings()
    if tf_symbols is not None:
        # Override TF symbols by patching the property's underlying string
        settings = settings.model_copy(update={
            "trend_follow_symbols": ",".join(tf_symbols),
        })

    original = sig_mod.check_trend_follow_exit
    sig_mod.check_trend_follow_exit = make_tf_exit_fn(trail_pct, use_death_cross)
    # engine imported the symbol; rebind there too
    from src.backtest import engine as eng
    eng.check_trend_follow_exit = sig_mod.check_trend_follow_exit

    try:
        result = run_backtest(klines, initial_capital=Decimal("10000"), settings=settings)
    finally:
        sig_mod.check_trend_follow_exit = original
        eng.check_trend_follow_exit = original

    tf_trades = [t for t in result.trades if t.strategy == "trend_follow"]
    tf_pnl = sum((t.pnl_usdt for t in tf_trades), Decimal("0"))
    tf_wins = sum(1 for t in tf_trades if t.pnl_usdt > 0)

    by_sym: dict[str, list] = defaultdict(list)
    for t in tf_trades:
        by_sym[t.symbol].append(t)

    by_exit: dict[str, list] = defaultdict(list)
    for t in tf_trades:
        by_exit[t.exit_reason].append(t)

    total_pnl = sum((t.pnl_usdt for t in result.trades), Decimal("0"))
    final_ret = (result.final_equity / Decimal("10000") - 1) * 100

    print(f"\n>>> {label}")
    print(f"    overall: trades={len(result.trades)}  total_pnl={float(total_pnl):+.2f}  "
          f"return={float(final_ret):+.2f}%")
    print(f"    TF only: n={len(tf_trades)}  W/L={tf_wins}/{len(tf_trades)-tf_wins}  "
          f"pnl={float(tf_pnl):+.2f}")
    if tf_trades:
        for sym in sorted(by_sym):
            ts = by_sym[sym]
            pnl = sum((t.pnl_usdt for t in ts), Decimal("0"))
            w = sum(1 for t in ts if t.pnl_usdt > 0)
            print(f"      {sym}: n={len(ts):3d} W/L={w}/{len(ts)-w:<2d} "
                  f"pnl={float(pnl):+8.2f}")
        print("    exits:", "  ".join(
            f"{r}={len(ts)}({float(sum((t.pnl_usdt for t in ts), Decimal('0'))):+.0f})"
            for r, ts in sorted(by_exit.items())
        ))


def main() -> None:
    print("Loading 2025-2026 data...")
    klines = load_data()
    print(f"  loaded {len(klines)} symbols, {min(len(v) for v in klines.values())} klines each")

    print("\n" + "=" * 70)
    print("BASELINE — current production (15% trail + death cross)")
    print("=" * 70)
    run_variant("baseline (15% trail + DC)", klines,
                trail_pct=Decimal("0.15"), use_death_cross=True)

    print("\n" + "=" * 70)
    print("VARIANT A — tighten trailing stop (keep death cross)")
    print("=" * 70)
    for tp in [Decimal("0.03"), Decimal("0.05"), Decimal("0.08"), Decimal("0.10")]:
        run_variant(f"trail={float(tp)*100:.0f}% + DC", klines,
                    trail_pct=tp, use_death_cross=True)

    print("\n" + "=" * 70)
    print("VARIANT B — trailing stop ONLY (no death cross)")
    print("=" * 70)
    for tp in [Decimal("0.03"), Decimal("0.05"), Decimal("0.08"), Decimal("0.10"), Decimal("0.15")]:
        run_variant(f"trail={float(tp)*100:.0f}% only", klines,
                    trail_pct=tp, use_death_cross=False)

    print("\n" + "=" * 70)
    print("VARIANT C — best variants with BTC dropped from TF")
    print("=" * 70)
    no_btc = ["ETHUSDC", "BNBUSDC", "SOLUSDC"]
    run_variant("3% trail only, TF=ETH/BNB/SOL", klines,
                trail_pct=Decimal("0.03"), use_death_cross=False, tf_symbols=no_btc)
    run_variant("3% trail + DC, TF=ETH/BNB/SOL", klines,
                trail_pct=Decimal("0.03"), use_death_cross=True, tf_symbols=no_btc)
    run_variant("baseline (15% + DC), TF=ETH/BNB/SOL", klines,
                trail_pct=Decimal("0.15"), use_death_cross=True, tf_symbols=no_btc)
    # also test TF=ETH only (most conservative)
    run_variant("3% trail only, TF=ETH only", klines,
                trail_pct=Decimal("0.03"), use_death_cross=False, tf_symbols=["ETHUSDC"])


if __name__ == "__main__":
    main()
