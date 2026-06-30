"""Max config (TF trail 4%) with ALL available tokens on every strategy.

MR, TF, and MOM each trade the full 12-token USDC universe. Other params
come from the live .env (slots 2/2/2, MOM TP 2.5%/SL 2.2%, MR EMA300).
Each year runs standalone with $10,000.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.backtest.data_loader import load_csv
from src.backtest.engine import BacktestTrade, run_backtest
from src.config.settings import Settings

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SYMBOLS = [
    "BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC",
    "ADAUSDC", "AVAXUSDC", "DOGEUSDC", "DOTUSDC",
    "LINKUSDC", "LTCUSDC", "SUIUSDC", "XRPUSDC",
]
INITIAL = Decimal("10000")
STRAT_LABEL = {"mean_reversion": "MR", "trend_follow": "TF", "momentum": "MOM"}
ALL = ",".join(SYMBOLS)


def load_year(year: str) -> dict[str, list]:
    by: dict[str, list] = {}
    for sym in SYMBOLS:
        p = os.path.join(DATA_DIR, f"{sym.lower()}_{year}_1h.csv")
        if os.path.exists(p):
            by[sym] = load_csv(p)
    return by


def stats(trades: list[BacktestTrade]) -> tuple[int, int, float, Decimal, Decimal]:
    n = len(trades)
    wins = sum(1 for t in trades if t.pnl_usdt > 0)
    pnl = sum((t.pnl_usdt for t in trades), Decimal("0"))
    wr = wins / n * 100 if n else 0.0
    avg = pnl / Decimal(n) if n else Decimal("0")
    return n, wins, wr, pnl, avg


def max_drawdown(equity_curve: list[Decimal]) -> float:
    peak = INITIAL
    max_dd = Decimal("0")
    for eq in equity_curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)
    return float(max_dd * 100)


def report(year: str, lines: list[str]) -> None:
    klines = load_year(year)
    if not klines:
        lines.append(f"\n[no data for {year}]")
        return

    settings = Settings().model_copy(update={
        "symbols": ALL,
        "mean_reversion_symbols": ALL,
        "trend_follow_symbols": ALL,
        "momentum_symbols": ALL,
        "trend_follow_trailing_stop_pct": Decimal("0.04"),
    })

    start_ts = max(k[0].open_time for k in klines.values())
    end_ts = min(k[-1].open_time for k in klines.values())
    fmt = lambda ts: datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d")

    result = run_backtest(klines, initial_capital=INITIAL, settings=settings)
    trades = result.trades
    n, wins, wr, pnl, _ = stats(trades)
    ret = (result.final_equity / INITIAL - 1) * 100

    lines.append("")
    lines.append("=" * 72)
    lines.append(f"  {year}  ({fmt(start_ts)} -> {fmt(end_ts)})  —  initial ${INITIAL:,.0f}")
    lines.append("=" * 72)
    lines.append(
        f"  TOTAL: trades={n}  W/L={wins}/{n - wins}  win-rate={wr:.1f}%  "
        f"PnL=${float(pnl):+,.2f}  return={float(ret):+.2f}%  "
        f"max-DD={max_drawdown(result.equity_curve):.2f}%"
    )

    by_strat: dict[str, list[BacktestTrade]] = defaultdict(list)
    by_sym: dict[str, list[BacktestTrade]] = defaultdict(list)
    by_ss: dict[tuple[str, str], list[BacktestTrade]] = defaultdict(list)
    for t in trades:
        by_strat[t.strategy].append(t)
        by_sym[t.symbol].append(t)
        by_ss[(t.strategy, t.symbol)].append(t)

    lines.append("")
    lines.append("  Per strategy:")
    lines.append(f"    {'strategy':<6} {'n':>4} {'W/L':>8} {'win%':>6} {'PnL $':>11} {'avg $':>8}")
    for strat in ["mean_reversion", "trend_follow", "momentum"]:
        ts = by_strat.get(strat, [])
        sn, sw, swr, sp, sa = stats(ts)
        lines.append(
            f"    {STRAT_LABEL[strat]:<6} {sn:>4} {f'{sw}/{sn - sw}':>8} "
            f"{swr:>5.1f}% {float(sp):>+11.2f} {float(sa):>+8.2f}"
        )

    lines.append("")
    lines.append("  Per token (sorted by PnL):")
    lines.append(f"    {'token':<10} {'n':>4} {'W/L':>8} {'win%':>6} {'PnL $':>11} {'avg $':>8}")
    tok_rows = []
    for sym in SYMBOLS:
        ts = by_sym.get(sym, [])
        sn, sw, swr, sp, sa = stats(ts)
        tok_rows.append((sp, sym, sn, sw, swr, sa))
    for sp, sym, sn, sw, swr, sa in sorted(tok_rows, reverse=True):
        lines.append(
            f"    {sym:<10} {sn:>4} {f'{sw}/{sn - sw}':>8} "
            f"{swr:>5.1f}% {float(sp):>+11.2f} {float(sa):>+8.2f}"
        )

    lines.append("")
    lines.append("  Per strategy x token (sorted by PnL within strategy):")
    lines.append(f"    {'strategy':<6} {'token':<10} {'n':>4} {'W/L':>8} {'win%':>6} {'PnL $':>11}")
    for strat in ["mean_reversion", "trend_follow", "momentum"]:
        rows = []
        for sym in SYMBOLS:
            ts = by_ss.get((strat, sym), [])
            if not ts:
                continue
            sn, sw, swr, sp, _ = stats(ts)
            rows.append((sp, sym, sn, sw, swr))
        for sp, sym, sn, sw, swr in sorted(rows, reverse=True):
            lines.append(
                f"    {STRAT_LABEL[strat]:<6} {sym:<10} {sn:>4} {f'{sw}/{sn - sw}':>8} "
                f"{swr:>5.1f}% {float(sp):>+11.2f}"
            )

    lines.append("")
    lines.append("  Exit reasons:")
    by_exit: dict[tuple[str, str], list[BacktestTrade]] = defaultdict(list)
    for t in trades:
        by_exit[(t.strategy, t.exit_reason)].append(t)
    for (strat, reason), ts in sorted(by_exit.items()):
        sn, sw, _, sp, _ = stats(ts)
        lines.append(
            f"    {STRAT_LABEL[strat]:<6} {reason:<14} n={sn:<4} W/L={sw}/{sn - sw:<4} "
            f"PnL=${float(sp):+,.2f}"
        )


def main() -> None:
    lines: list[str] = []
    lines.append("ALL-TOKEN BACKTEST — max config (TF trail 4%), 12-token universe")
    lines.append(f"  universe: {ALL}")
    lines.append("  MR/TF/MOM all trade every token; slots 2/2/2; fee 0.1%")

    for year in ["2025", "2026"]:
        report(year, lines)

    text = "\n".join(lines)
    print(text)

    out = os.path.join(DATA_DIR, "reports", "all_tokens_trail4.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(text + "\n")
    print(f"\nsaved to {out}")


if __name__ == "__main__":
    main()
