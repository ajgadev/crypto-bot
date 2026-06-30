"""Yearly backtest report using the current live config (.env).

Runs 2025 and 2026 separately, each starting with $10,000, and breaks
results down per strategy, per token, and per strategy x token.
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
SYMBOLS = ["BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC"]
INITIAL = Decimal("10000")
STRAT_LABEL = {
    "mean_reversion": "MR",
    "trend_follow": "TF",
    "momentum": "MOM",
}


def load_year(year: str) -> dict[str, list]:
    by: dict[str, list] = {}
    for sym in SYMBOLS:
        p = os.path.join(DATA_DIR, f"{sym.lower()}_{year}_1h.csv")
        if os.path.exists(p):
            by[sym] = load_csv(p)
    return by


def stats(trades: list[BacktestTrade]) -> tuple[int, int, float, Decimal, Decimal]:
    """Return (n, wins, win_rate, pnl, avg_pnl)."""
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
        dd = (peak - eq) / peak
        max_dd = max(max_dd, dd)
    return float(max_dd * 100)


def report(year: str, lines: list[str]) -> None:
    klines = load_year(year)
    if not klines:
        lines.append(f"\n[no data for {year}]")
        return

    start_ts = max(k[0].open_time for k in klines.values())
    end_ts = min(k[-1].open_time for k in klines.values())
    fmt = lambda ts: datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d")

    result = run_backtest(klines, initial_capital=INITIAL, settings=Settings())
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
    lines.append("  Per token:")
    lines.append(f"    {'token':<9} {'n':>4} {'W/L':>8} {'win%':>6} {'PnL $':>11} {'avg $':>8}")
    for sym in SYMBOLS:
        ts = by_sym.get(sym, [])
        sn, sw, swr, sp, sa = stats(ts)
        lines.append(
            f"    {sym:<9} {sn:>4} {f'{sw}/{sn - sw}':>8} "
            f"{swr:>5.1f}% {float(sp):>+11.2f} {float(sa):>+8.2f}"
        )

    lines.append("")
    lines.append("  Per strategy x token:")
    lines.append(f"    {'strategy':<6} {'token':<9} {'n':>4} {'W/L':>8} {'win%':>6} {'PnL $':>11}")
    for strat in ["mean_reversion", "trend_follow", "momentum"]:
        for sym in SYMBOLS:
            ts = by_ss.get((strat, sym), [])
            if not ts:
                continue
            sn, sw, swr, sp, _ = stats(ts)
            lines.append(
                f"    {STRAT_LABEL[strat]:<6} {sym:<9} {sn:>4} {f'{sw}/{sn - sw}':>8} "
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
    s = Settings()
    lines: list[str] = []
    lines.append("YEARLY BACKTEST REPORT — current live config")
    lines.append(
        f"  MR : {','.join(s.mean_reversion_symbols_list)}  "
        f"TP {float(s.take_profit_pct) * 100:.1f}% / SL {float(s.stop_loss_pct) * 100:.1f}% / "
        f"RSI<{s.mean_reversion_rsi_max} / drop<{float(s.mean_reversion_pct_drop) * 100:.0f}% / "
        f"trend EMA{s.mean_reversion_trend_ema}"
    )
    lines.append(
        f"  TF : {','.join(s.trend_follow_symbols_list)}  "
        f"trail {float(s.trend_follow_trailing_stop_pct) * 100:.0f}%"
        f"{' + death-cross' if s.trend_follow_use_death_cross else ' (trail only)'}"
    )
    lines.append(
        f"  MOM: {','.join(s.momentum_symbols_list)}  "
        f"TP {float(s.momentum_take_profit_pct) * 100:.1f}% / SL {float(s.momentum_stop_loss_pct) * 100:.1f}%"
    )
    lines.append(
        f"  slots {s.max_open_trades}/{s.trend_follow_max_trades}/{s.momentum_max_trades}  "
        f"order={s.strategy_order}  budget-alloc={'on' if s.budget_allocation_enabled else 'off'}  "
        f"fee=0.1%"
    )

    for year in ["2025", "2026"]:
        report(year, lines)

    text = "\n".join(lines)
    print(text)

    out = os.path.join(DATA_DIR, "reports", "yearly_report_2025_2026.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(text + "\n")
    print(f"\nsaved to {out}")


if __name__ == "__main__":
    main()
