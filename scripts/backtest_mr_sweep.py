"""MR-only parameter sweep.

Goal: figure out why MR barely trades and what relaxations actually
produce entries (and whether those entries make money).

Sweeps over:
  - RSI_MAX
  - PCT_DROP
  - TREND_EMA (with option to disable the trend filter)
  - Symbol set (current core 4 vs adding alts)

TF and MOM are disabled so we measure MR in isolation. Window matches
backtest_symbol_sweep.py: 2025-01-01 → 2026-02-28 (where every candidate
CSV has coverage).
"""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from src.backtest.data_loader import load_csv
from src.backtest.engine import run_backtest
from src.config.settings import Settings

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
START_MS = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = int(datetime(2026, 2, 28, tzinfo=timezone.utc).timestamp() * 1000)

CORE = ["BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC"]
ALTS = ["ADAUSDC", "AVAXUSDC", "LINKUSDC", "XRPUSDC", "DOGEUSDC", "DOTUSDC"]


def load_klines() -> dict[str, list]:
    """Load 1h CSVs for CORE + ALTS within the window."""
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
    for sym in ALTS:
        p = os.path.join(DATA_DIR, f"{sym.lower()}_full_1h.csv")
        if not os.path.exists(p):
            continue
        ks = [k for k in load_csv(p) if START_MS <= k.open_time <= END_MS]
        ks.sort(key=lambda k: k.open_time)
        if ks:
            by[sym] = ks
    return by


@dataclass
class Variant:
    label: str
    rsi_max: Decimal
    pct_drop: Decimal
    trend_ema: int
    trend_filter: bool
    symbols: list[str]
    # Exit-side knobs (defaults match current .env)
    tp_pct: Decimal = Decimal("0.03")
    sl_pct: Decimal = Decimal("0.02")
    rsi_exit: Decimal = Decimal("70")
    regime_adaptive: bool = False
    regime_ema: int = 200
    # Bear-market overrides; default None = use baseline (no behavioral switch)
    bear_rsi_max: Decimal | None = None
    bear_pct_drop: Decimal | None = None
    bear_tp_pct: Decimal | None = None
    bear_sl_pct: Decimal | None = None


def run_variant(v: Variant, klines: dict[str, list]) -> dict:
    """Run a single MR variant (TF/MOM disabled) and print a one-block report."""
    settings = Settings().model_copy(update={
        # Strategy toggles — MR only
        "mean_reversion_enabled": True,
        "trend_follow_enabled": False,
        "momentum_enabled": False,
        # MR entry params under test
        "mean_reversion_symbols": ",".join(v.symbols),
        "mean_reversion_rsi_max": v.rsi_max,
        "mean_reversion_pct_drop": v.pct_drop,
        "mean_reversion_trend_filter": v.trend_filter,
        "mean_reversion_trend_ema": v.trend_ema,
        # MR exit params under test
        "mean_reversion_rsi_exit": v.rsi_exit,
        "take_profit_pct": v.tp_pct,
        "stop_loss_pct": v.sl_pct,
        # Regime-adaptive toggle + bear-param overrides (None → mirror baseline)
        "mean_reversion_regime_adaptive": v.regime_adaptive,
        "mean_reversion_regime_ema": v.regime_ema,
        "mean_reversion_bear_rsi_max": v.bear_rsi_max if v.bear_rsi_max is not None else v.rsi_max,
        "mean_reversion_bear_pct_drop": v.bear_pct_drop if v.bear_pct_drop is not None else v.pct_drop,
        "mean_reversion_bear_tp_pct": v.bear_tp_pct if v.bear_tp_pct is not None else v.tp_pct,
        "mean_reversion_bear_sl_pct": v.bear_sl_pct if v.bear_sl_pct is not None else v.sl_pct,
        "defensive_mode_enabled": False,
        # Single shared pool so MR gets full tradable budget
        "budget_allocation_enabled": False,
        "strategy_order": "mr",
        "max_open_trades": 2,
    })

    sub = {s: klines[s] for s in v.symbols if s in klines}
    if not sub:
        print(f"  {v.label}: no klines loaded for {v.symbols}")
        return {}

    result = run_backtest(sub, initial_capital=Decimal("10000"), settings=settings)

    trades = [t for t in result.trades if t.strategy == "mean_reversion"]
    total_pnl = sum((t.pnl_usdt for t in trades), Decimal("0"))
    wins = sum(1 for t in trades if t.pnl_usdt > 0)
    losses = len(trades) - wins
    ret = (result.final_equity / Decimal("10000") - 1) * 100
    avg_hold = (
        sum(t.holding_hours for t in trades) / len(trades) if trades else 0
    )
    by_reason: dict[str, int] = defaultdict(int)
    for t in trades:
        by_reason[t.exit_reason] += 1

    print(f"\n>>> {v.label}")
    print(
        f"    n={len(trades):<3d}  W/L={wins}/{losses:<3d}  "
        f"pnl={float(total_pnl):+8.2f}  return={float(ret):+6.2f}%  "
        f"avg_hold={avg_hold:.1f}h"
    )
    if trades:
        reasons = "  ".join(f"{r}={c}" for r, c in sorted(by_reason.items()))
        print(f"    exits: {reasons}")
        # Per-symbol breakdown
        by_sym: dict[str, list] = defaultdict(list)
        for t in trades:
            by_sym[t.symbol].append(t)
        for sym in sorted(by_sym):
            ts = by_sym[sym]
            pnl = sum((t.pnl_usdt for t in ts), Decimal("0"))
            w = sum(1 for t in ts if t.pnl_usdt > 0)
            print(
                f"      {sym:8s} n={len(ts):3d}  W/L={w}/{len(ts)-w:<3d}  "
                f"pnl={float(pnl):+8.2f}"
            )

    return {
        "label": v.label,
        "trades": len(trades),
        "wins": wins,
        "pnl": float(total_pnl),
        "return_pct": float(ret),
    }


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main() -> None:
    print("Loading 2025-01 → 2026-02 data...")
    klines = load_klines()
    for s, ks in klines.items():
        print(f"  {s}: {len(ks)} klines")

    # Current prod baseline (matches .env)
    baseline = Variant(
        label="BASELINE (rsi<50, drop<-1%, EMA300, core 4)",
        rsi_max=Decimal("50"),
        pct_drop=Decimal("-0.01"),
        trend_ema=300,
        trend_filter=True,
        symbols=CORE,
    )

    section("BASELINE — current prod MR config in isolation")
    rows: list[dict] = []
    rows.append(run_variant(baseline, klines))

    # ── Knob 1: RSI_MAX ──
    section("Sweep RSI_MAX (others = baseline)")
    for rsi_max in [Decimal("45"), Decimal("50"), Decimal("55"), Decimal("60")]:
        v = Variant(
            label=f"RSI_MAX={rsi_max}",
            rsi_max=rsi_max,
            pct_drop=baseline.pct_drop,
            trend_ema=baseline.trend_ema,
            trend_filter=True,
            symbols=baseline.symbols,
        )
        rows.append(run_variant(v, klines))

    # ── Knob 2: PCT_DROP ──
    section("Sweep PCT_DROP (others = baseline)")
    for drop in [
        Decimal("-0.003"),
        Decimal("-0.005"),
        Decimal("-0.01"),
        Decimal("-0.015"),
        Decimal("-0.02"),
    ]:
        v = Variant(
            label=f"PCT_DROP={drop}",
            rsi_max=baseline.rsi_max,
            pct_drop=drop,
            trend_ema=baseline.trend_ema,
            trend_filter=True,
            symbols=baseline.symbols,
        )
        rows.append(run_variant(v, klines))

    # ── Knob 3: Trend filter ──
    section("Sweep TREND filter (others = baseline)")
    for trend_ema, trend_on, label in [
        (100, True,  "TREND_EMA=100"),
        (200, True,  "TREND_EMA=200"),
        (300, True,  "TREND_EMA=300 (current)"),
        (300, False, "TREND_FILTER=off"),
    ]:
        v = Variant(
            label=label,
            rsi_max=baseline.rsi_max,
            pct_drop=baseline.pct_drop,
            trend_ema=trend_ema,
            trend_filter=trend_on,
            symbols=baseline.symbols,
        )
        rows.append(run_variant(v, klines))

    # ── Knob 4: Symbol expansion ──
    section("Sweep symbols (baseline params, add alts one at a time)")
    for cand in ALTS:
        if cand not in klines:
            continue
        v = Variant(
            label=f"core + {cand}",
            rsi_max=baseline.rsi_max,
            pct_drop=baseline.pct_drop,
            trend_ema=baseline.trend_ema,
            trend_filter=True,
            symbols=CORE + [cand],
        )
        rows.append(run_variant(v, klines))

    # ── Promising combo grid ──
    # Cross RSI_MAX with PCT_DROP at the "loosened but not silly" levels
    section("Combo grid: RSI_MAX × PCT_DROP (EMA300, core 4)")
    for rsi_max in [Decimal("50"), Decimal("55"), Decimal("60")]:
        for drop in [Decimal("-0.005"), Decimal("-0.01"), Decimal("-0.015")]:
            v = Variant(
                label=f"rsi<{rsi_max}  drop<{drop}",
                rsi_max=rsi_max,
                pct_drop=drop,
                trend_ema=baseline.trend_ema,
                trend_filter=True,
                symbols=baseline.symbols,
            )
            rows.append(run_variant(v, klines))

    # ── Best-shot variant: loosened + all symbols ──
    section("Best-shot: rsi<55, drop<-0.5%, EMA200, core + all alts")
    expanded = CORE + [s for s in ALTS if s in klines]
    v = Variant(
        label="loosened + expanded",
        rsi_max=Decimal("55"),
        pct_drop=Decimal("-0.005"),
        trend_ema=200,
        trend_filter=True,
        symbols=expanded,
    )
    rows.append(run_variant(v, klines))

    # ── Prune losing symbols (per-symbol PnL was sharply bimodal) ──
    section("Symbol pruning (baseline params on subsets of CORE)")
    for subset, label in [
        (["BNBUSDC"],                       "BNB only"),
        (["BNBUSDC", "BTCUSDC"],            "BNB + BTC"),
        (["BNBUSDC", "ETHUSDC"],            "BNB + ETH"),
        (["BNBUSDC", "BTCUSDC", "ETHUSDC"], "no SOL"),
        (["BNBUSDC", "BTCUSDC", "SOLUSDC"], "no ETH"),
    ]:
        v = Variant(
            label=label,
            rsi_max=baseline.rsi_max,
            pct_drop=baseline.pct_drop,
            trend_ema=baseline.trend_ema,
            trend_filter=True,
            symbols=subset,
        )
        rows.append(run_variant(v, klines))

    # ── Tight drop filter on pruned set (PCT_DROP=-0.02 was the only winner) ──
    section("Tight drop (-2%) on pruned symbol subsets")
    for subset, label in [
        (["BNBUSDC"],                       "tight: BNB only"),
        (["BNBUSDC", "BTCUSDC"],            "tight: BNB + BTC"),
        (["BNBUSDC", "BTCUSDC", "ETHUSDC"], "tight: no SOL"),
        (CORE,                              "tight: core 4"),
    ]:
        v = Variant(
            label=label,
            rsi_max=baseline.rsi_max,
            pct_drop=Decimal("-0.02"),
            trend_ema=baseline.trend_ema,
            trend_filter=True,
            symbols=subset,
        )
        rows.append(run_variant(v, klines))

    # ── Exit-side sweeps (entries = baseline) ──
    section("Sweep STOP_LOSS (entries = baseline, TP=3%, RSI_EXIT=70)")
    for sl in [Decimal("0.02"), Decimal("0.025"), Decimal("0.03"),
               Decimal("0.04"), Decimal("0.05"), Decimal("0.06")]:
        v = Variant(
            label=f"SL={sl}",
            rsi_max=baseline.rsi_max, pct_drop=baseline.pct_drop,
            trend_ema=baseline.trend_ema, trend_filter=True,
            symbols=baseline.symbols, sl_pct=sl,
        )
        rows.append(run_variant(v, klines))

    section("Sweep TAKE_PROFIT (entries = baseline, SL=2%, RSI_EXIT=70)")
    for tp in [Decimal("0.02"), Decimal("0.025"), Decimal("0.03"),
               Decimal("0.04"), Decimal("0.05"), Decimal("0.06")]:
        v = Variant(
            label=f"TP={tp}",
            rsi_max=baseline.rsi_max, pct_drop=baseline.pct_drop,
            trend_ema=baseline.trend_ema, trend_filter=True,
            symbols=baseline.symbols, tp_pct=tp,
        )
        rows.append(run_variant(v, klines))

    section("Sweep RSI_EXIT (entries = baseline)")
    for rx in [Decimal("55"), Decimal("60"), Decimal("65"),
               Decimal("70"), Decimal("75"), Decimal("80")]:
        v = Variant(
            label=f"RSI_EXIT={rx}",
            rsi_max=baseline.rsi_max, pct_drop=baseline.pct_drop,
            trend_ema=baseline.trend_ema, trend_filter=True,
            symbols=baseline.symbols, rsi_exit=rx,
        )
        rows.append(run_variant(v, klines))

    # ── TP/SL grid (R:R matters most for thin-edge strategies) ──
    section("TP × SL grid (entries = baseline)")
    for tp in [Decimal("0.025"), Decimal("0.03"), Decimal("0.04"), Decimal("0.05")]:
        for sl in [Decimal("0.02"), Decimal("0.03"), Decimal("0.04"), Decimal("0.05")]:
            v = Variant(
                label=f"TP={tp} SL={sl}",
                rsi_max=baseline.rsi_max, pct_drop=baseline.pct_drop,
                trend_ema=baseline.trend_ema, trend_filter=True,
                symbols=baseline.symbols, tp_pct=tp, sl_pct=sl,
            )
            rows.append(run_variant(v, klines))

    # ── Regime-adaptive (uses bear params from .env) ──
    section("Regime-adaptive (bull → baseline, bear → bear_* params from .env)")
    v = Variant(
        label="regime-adaptive ON",
        rsi_max=baseline.rsi_max, pct_drop=baseline.pct_drop,
        trend_ema=baseline.trend_ema, trend_filter=True,
        symbols=baseline.symbols, regime_adaptive=True,
    )
    rows.append(run_variant(v, klines))

    # ── TREND_EMA sweep ON TOP OF WINNING BASE (TP=2.5%) ──
    section("Sweep TREND_EMA on winning base (TP=2.5%, otherwise baseline)")
    for trend_ema in [50, 100, 150, 200, 250, 300, 400]:
        v = Variant(
            label=f"TP=2.5% TREND_EMA={trend_ema}",
            rsi_max=baseline.rsi_max, pct_drop=baseline.pct_drop,
            trend_ema=trend_ema, trend_filter=True,
            symbols=baseline.symbols, tp_pct=Decimal("0.025"),
        )
        rows.append(run_variant(v, klines))
    # Also "filter off" on winning base
    v = Variant(
        label="TP=2.5% TREND_FILTER=off",
        rsi_max=baseline.rsi_max, pct_drop=baseline.pct_drop,
        trend_ema=300, trend_filter=False,
        symbols=baseline.symbols, tp_pct=Decimal("0.025"),
    )
    rows.append(run_variant(v, klines))

    # ── REGIME_EMA sweep, ISOLATED ──
    # Regime EMA only matters when regime-adaptive is ON. To isolate the EMA
    # period itself, hold bear params == baseline params (so flipping into
    # "bear" mode is a no-op behaviorally) and verify those variants are all
    # equal to baseline. Then re-run with realistic bear params and sweep.
    section("REGIME_EMA sweep — control (bear == baseline params, expect 0 effect)")
    for regime_ema in [100, 200, 300]:
        v = Variant(
            label=f"regime_ema={regime_ema} (bear==baseline)",
            rsi_max=baseline.rsi_max, pct_drop=baseline.pct_drop,
            trend_ema=baseline.trend_ema, trend_filter=True,
            symbols=baseline.symbols,
            regime_adaptive=True, regime_ema=regime_ema,
            # bear_* all None → forwarded as baseline params
        )
        rows.append(run_variant(v, klines))

    section("REGIME_EMA sweep — realistic bear (tighter TP/SL=2% in bear)")
    # In bear: tighter TP/SL = 2% (faster lock-in). Bull = baseline.
    for regime_ema in [100, 150, 200, 250, 300]:
        v = Variant(
            label=f"regime_ema={regime_ema} (bear TP/SL=2%)",
            rsi_max=baseline.rsi_max, pct_drop=baseline.pct_drop,
            trend_ema=baseline.trend_ema, trend_filter=True,
            symbols=baseline.symbols,
            regime_adaptive=True, regime_ema=regime_ema,
            bear_tp_pct=Decimal("0.02"), bear_sl_pct=Decimal("0.02"),
        )
        rows.append(run_variant(v, klines))

    section("REGIME_EMA sweep — winning-base bull (TP=2.5%) + bear TP=2% SL=2%")
    for regime_ema in [100, 150, 200, 250, 300]:
        v = Variant(
            label=f"TP=2.5% regime_ema={regime_ema} (bear TP/SL=2%)",
            rsi_max=baseline.rsi_max, pct_drop=baseline.pct_drop,
            trend_ema=baseline.trend_ema, trend_filter=True,
            symbols=baseline.symbols, tp_pct=Decimal("0.025"),
            regime_adaptive=True, regime_ema=regime_ema,
            bear_tp_pct=Decimal("0.02"), bear_sl_pct=Decimal("0.02"),
        )
        rows.append(run_variant(v, klines))

    # ── Best-of-knobs synthesis: combine the winners from each axis ──
    section("Synthesis: best knobs combined")
    syntheses = [
        # (label, symbols, pct_drop, tp, sl, rsi_exit)
        ("no SOL, baseline exits",
         ["BNBUSDC", "BTCUSDC", "ETHUSDC"],
         Decimal("-0.01"), Decimal("0.03"), Decimal("0.02"), Decimal("70")),
        ("no SOL, wider SL=4%",
         ["BNBUSDC", "BTCUSDC", "ETHUSDC"],
         Decimal("-0.01"), Decimal("0.03"), Decimal("0.04"), Decimal("70")),
        ("no SOL, TP=4% SL=4%",
         ["BNBUSDC", "BTCUSDC", "ETHUSDC"],
         Decimal("-0.01"), Decimal("0.04"), Decimal("0.04"), Decimal("70")),
        ("no SOL, tight drop -2%, wider SL=4%",
         ["BNBUSDC", "BTCUSDC", "ETHUSDC"],
         Decimal("-0.02"), Decimal("0.03"), Decimal("0.04"), Decimal("70")),
        ("BNB-only, baseline exits",
         ["BNBUSDC"],
         Decimal("-0.01"), Decimal("0.03"), Decimal("0.02"), Decimal("70")),
        ("BNB-only, wider SL=4%",
         ["BNBUSDC"],
         Decimal("-0.01"), Decimal("0.03"), Decimal("0.04"), Decimal("70")),
        ("core4, wider SL=4%",
         CORE,
         Decimal("-0.01"), Decimal("0.03"), Decimal("0.04"), Decimal("70")),
        ("core4, TP=5% SL=5%",
         CORE,
         Decimal("-0.01"), Decimal("0.05"), Decimal("0.05"), Decimal("70")),
    ]
    for label, syms, drop, tp, sl, rx in syntheses:
        v = Variant(
            label=label, rsi_max=baseline.rsi_max, pct_drop=drop,
            trend_ema=baseline.trend_ema, trend_filter=True,
            symbols=syms, tp_pct=tp, sl_pct=sl, rsi_exit=rx,
        )
        rows.append(run_variant(v, klines))

    # ── Final ranked summary ──
    section("RANKED SUMMARY (by PnL)")
    rows = [r for r in rows if r]
    rows.sort(key=lambda r: r["pnl"], reverse=True)
    print(f"  {'label':52s}  {'n':>4s}  {'W/L':>7s}  {'pnl':>10s}  {'ret%':>7s}")
    for r in rows:
        wl = f"{r['wins']}/{r['trades'] - r['wins']}"
        print(
            f"  {r['label']:52.52s}  {r['trades']:4d}  {wl:>7s}  "
            f"{r['pnl']:+10.2f}  {r['return_pct']:+7.2f}"
        )


if __name__ == "__main__":
    main()
