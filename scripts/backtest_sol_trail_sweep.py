"""Sweep: TF trailing-stop width x SOL removal from TF/MOM.

Uses the intra-candle fill engine and the current live config as baseline
(MR untouched: BTC,ETH,BNB,SOL). Each year runs standalone with $10,000.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.backtest.data_loader import load_csv
from src.backtest.engine import run_backtest
from src.config.settings import Settings

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SYMBOLS = ["BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC"]
INITIAL = Decimal("10000")

TRAILS = [Decimal("0.03"), Decimal("0.04"), Decimal("0.05")]
COMBOS = [
    # (label, tf_symbols, mom_symbols)
    ("baseline       ", "ETHUSDC,BNBUSDC,SOLUSDC", "ETHUSDC,SOLUSDC,BNBUSDC"),
    ("TF-noSOL       ", "ETHUSDC,BNBUSDC", "ETHUSDC,SOLUSDC,BNBUSDC"),
    ("MOM-noSOL      ", "ETHUSDC,BNBUSDC,SOLUSDC", "ETHUSDC,BNBUSDC"),
    ("both-noSOL     ", "ETHUSDC,BNBUSDC", "ETHUSDC,BNBUSDC"),
]


def load_year(year: str) -> dict[str, list]:
    by: dict[str, list] = {}
    for sym in SYMBOLS:
        p = os.path.join(DATA_DIR, f"{sym.lower()}_{year}_1h.csv")
        if os.path.exists(p):
            by[sym] = load_csv(p)
    return by


def max_drawdown(equity_curve: list[Decimal]) -> float:
    peak = INITIAL
    max_dd = Decimal("0")
    for eq in equity_curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)
    return float(max_dd * 100)


def run_one(klines: dict[str, list], trail: Decimal, tf_syms: str, mom_syms: str):
    settings = Settings().model_copy(update={
        "trend_follow_trailing_stop_pct": trail,
        "trend_follow_symbols": tf_syms,
        "momentum_symbols": mom_syms,
    })
    return run_backtest(klines, initial_capital=INITIAL, settings=settings)


def main() -> None:
    lines: list[str] = []
    lines.append("SWEEP: TF trail % x SOL removal (intra-candle engine, $10k/year)")
    lines.append("MR fixed: BTCUSDC,ETHUSDC,BNBUSDC,SOLUSDC")

    # (label, trail) -> {year: (ret_pct, dd, mr, tf, mom, n)}
    results: dict[tuple[str, Decimal], dict[str, tuple]] = defaultdict(dict)

    for year in ["2025", "2026"]:
        klines = load_year(year)
        if not klines:
            continue
        lines.append("")
        lines.append("=" * 78)
        lines.append(f"  {year}")
        lines.append("=" * 78)
        lines.append(
            f"  {'config':<16} {'trail':>5} {'n':>4} {'ret%':>7} {'maxDD%':>7} "
            f"{'MR $':>9} {'TF $':>9} {'MOM $':>9}"
        )
        for label, tf_syms, mom_syms in COMBOS:
            for trail in TRAILS:
                res = run_one(klines, trail, tf_syms, mom_syms)
                ret = float((res.final_equity / INITIAL - 1) * 100)
                dd = max_drawdown(res.equity_curve)
                pnl_by = defaultdict(Decimal)
                for t in res.trades:
                    pnl_by[t.strategy] += t.pnl_usdt
                row = (
                    ret, dd,
                    float(pnl_by["mean_reversion"]),
                    float(pnl_by["trend_follow"]),
                    float(pnl_by["momentum"]),
                    len(res.trades),
                )
                results[(label, trail)][year] = row
                lines.append(
                    f"  {label:<16} {float(trail) * 100:>4.0f}% {row[5]:>4} {ret:>+7.2f} {dd:>7.2f} "
                    f"{row[2]:>+9.2f} {row[3]:>+9.2f} {row[4]:>+9.2f}"
                )
                print(lines[-1], flush=True)

    # Combined ranking: sum of both years' returns
    lines.append("")
    lines.append("=" * 78)
    lines.append("  RANKED (2025 ret% + 2026 ret%)")
    lines.append("=" * 78)
    lines.append(
        f"  {'config':<16} {'trail':>5} {'2025 ret%':>10} {'2026 ret%':>10} "
        f"{'sum':>8} {'worst DD%':>9}"
    )
    ranked = sorted(
        results.items(),
        key=lambda kv: -(kv[1].get("2025", (0,))[0] + kv[1].get("2026", (0,))[0]),
    )
    for (label, trail), by_year in ranked:
        r25 = by_year.get("2025", (0.0, 0.0))
        r26 = by_year.get("2026", (0.0, 0.0))
        lines.append(
            f"  {label:<16} {float(trail) * 100:>4.0f}% {r25[0]:>+10.2f} {r26[0]:>+10.2f} "
            f"{r25[0] + r26[0]:>+8.2f} {max(r25[1], r26[1]):>9.2f}"
        )

    text = "\n".join(lines)
    print()
    print(text)

    out = os.path.join(DATA_DIR, "reports", "sweep_sol_trail.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(text + "\n")
    print(f"\nsaved to {out}")


if __name__ == "__main__":
    main()
