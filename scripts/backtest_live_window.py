"""Validate proposed TF changes on the actual live window: 2026-02-25 to 2026-04-29.

Compares baseline (current prod) vs proposed (3% trail only + drop BTC from TF).
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
SYMBOLS = ["BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC"]

START_MS = int(datetime(2026, 2, 25, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = int(datetime(2026, 4, 29, tzinfo=timezone.utc).timestamp() * 1000)


def load_window() -> dict[str, list]:
    by_sym: dict[str, list] = {}
    for sym in SYMBOLS:
        path = os.path.join(DATA_DIR, f"{sym.lower()}_2026_1h.csv")
        if not os.path.exists(path):
            continue
        ks = load_csv(path)
        # Need warmup before window: keep candles from start of file up to window end,
        # the engine handles its own 50-candle warmup. Use only the window plus prior context.
        ks = [k for k in ks if k.open_time <= END_MS]
        ks.sort(key=lambda k: k.open_time)
        by_sym[sym] = ks
    return by_sym


def make_tf_exit_fn(trail_pct: Decimal | None, use_death_cross: bool):
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
        settings = settings.model_copy(update={
            "trend_follow_symbols": ",".join(tf_symbols),
        })

    original = sig_mod.check_trend_follow_exit
    sig_mod.check_trend_follow_exit = make_tf_exit_fn(trail_pct, use_death_cross)
    from src.backtest import engine as eng
    eng.check_trend_follow_exit = sig_mod.check_trend_follow_exit

    try:
        result = run_backtest(klines, initial_capital=Decimal("10000"), settings=settings)
    finally:
        sig_mod.check_trend_follow_exit = original
        eng.check_trend_follow_exit = original

    # Filter trades to those entered within the live window
    in_window = [t for t in result.trades if START_MS <= t.entry_time <= END_MS]
    total_pnl = sum((t.pnl_usdt for t in in_window), Decimal("0"))
    wins = sum(1 for t in in_window if t.pnl_usdt > 0)

    print(f"\n>>> {label}")
    print(f"    Window 2026-02-25 → 2026-04-29")
    print(f"    Trades: {len(in_window)}  W/L: {wins}/{len(in_window)-wins}  "
          f"PnL: ${float(total_pnl):+.2f}")

    by_strat: dict[str, list] = defaultdict(list)
    for t in in_window:
        by_strat[t.strategy].append(t)
    for strat in sorted(by_strat):
        ts = by_strat[strat]
        pnl = sum((t.pnl_usdt for t in ts), Decimal("0"))
        w = sum(1 for t in ts if t.pnl_usdt > 0)
        print(f"      {strat:15s} n={len(ts):3d}  W/L={w}/{len(ts)-w:<3d}  "
              f"pnl={float(pnl):+8.2f}")

    by_ss: dict[tuple, list] = defaultdict(list)
    for t in in_window:
        by_ss[(t.strategy, t.symbol)].append(t)
    print("    by strategy x symbol:")
    for (strat, sym), ts in sorted(by_ss.items()):
        pnl = sum((t.pnl_usdt for t in ts), Decimal("0"))
        w = sum(1 for t in ts if t.pnl_usdt > 0)
        print(f"      {strat:15s} {sym:8s} n={len(ts):3d}  W/L={w}/{len(ts)-w:<3d}  "
              f"pnl={float(pnl):+8.2f}")


def main() -> None:
    print("Loading 2026 data (will only count trades in 2026-02-25 → 2026-04-29)...")
    klines = load_window()
    for s, ks in klines.items():
        print(f"  {s}: {len(ks)} klines")

    print("\n" + "=" * 70)
    print("BASELINE — current production")
    print("=" * 70)
    run_variant("current prod (15% trail + DC, TF=BTC/ETH/BNB/SOL)", klines,
                trail_pct=Decimal("0.15"), use_death_cross=True)

    print("\n" + "=" * 70)
    print("PROPOSED")
    print("=" * 70)
    run_variant("3% trail only, TF=ETH/BNB/SOL", klines,
                trail_pct=Decimal("0.03"), use_death_cross=False,
                tf_symbols=["ETHUSDC", "BNBUSDC", "SOLUSDC"])
    run_variant("3% trail + DC (safety net), TF=ETH/BNB/SOL", klines,
                trail_pct=Decimal("0.03"), use_death_cross=True,
                tf_symbols=["ETHUSDC", "BNBUSDC", "SOLUSDC"])


if __name__ == "__main__":
    main()
