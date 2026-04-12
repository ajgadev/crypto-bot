"""Backtest each strategy x token combo independently for 2026 data."""

from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.backtest.data_loader import load_csv
from src.backtest.engine import run_backtest
from src.config.settings import Settings

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
INITIAL_CAPITAL = Decimal("1000")

SYMBOL_FILES = {
    "BTCUSDC": "btcusdc_2026_1h.csv",
    "ETHUSDC": "ethusdc_2026_1h.csv",
    "BNBUSDC": "bnbusdc_2026_1h.csv",
    "SOLUSDC": "solusdc_2026_1h.csv",
    "XRPUSDC": "xrpusdc_2026_1h.csv",
}

STRATEGIES = {
    "mr": "Mean Reversion",
    "tf": "Trend Follow",
    "mom": "Momentum",
}


def make_settings(strategy: str, symbol: str) -> Settings:
    """Create settings enabling only one strategy for one symbol."""
    return Settings(
        symbols=symbol,
        binance_api_key="test",
        binance_api_secret="test",
        mean_reversion_enabled=(strategy == "mr"),
        trend_follow_enabled=(strategy == "tf"),
        momentum_enabled=(strategy == "mom"),
        momentum_symbols=symbol,
        max_open_trades=2,
        trend_follow_max_trades=2,
        momentum_max_trades=2,
        budget_allocation_enabled=False,
        defensive_mode_enabled=False,
        mean_reversion_regime_adaptive=False,
        strategy_order=strategy,
    )


def main() -> None:
    print("Loading 2026 data...")
    klines = {}
    for symbol, fname in SYMBOL_FILES.items():
        path = os.path.join(DATA_DIR, fname)
        if os.path.exists(path):
            klines[symbol] = load_csv(path)
            print(f"  {symbol}: {len(klines[symbol])} candles")

    results = []

    for strat_key, strat_name in STRATEGIES.items():
        for symbol in klines:
            settings = make_settings(strat_key, symbol)
            symbol_data = {symbol: klines[symbol]}

            result = run_backtest(symbol_data, INITIAL_CAPITAL, settings)
            trades = [t for t in result.trades if t.symbol == symbol]

            n = len(trades)
            if n == 0:
                results.append({
                    "strategy": strat_name, "strat_key": strat_key, "symbol": symbol,
                    "trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "total_pnl": 0, "avg_pnl": 0, "best": 0, "worst": 0,
                    "avg_hold_hrs": 0, "exit_reasons": {}, "return_pct": 0,
                })
                continue

            wins = sum(1 for t in trades if t.pnl_usdt > 0)
            total_pnl = sum(float(t.pnl_usdt) for t in trades)
            exit_reasons = {}
            for t in trades:
                exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

            results.append({
                "strategy": strat_name,
                "strat_key": strat_key,
                "symbol": symbol,
                "trades": n,
                "wins": wins,
                "losses": n - wins,
                "win_rate": wins / n * 100,
                "total_pnl": total_pnl,
                "avg_pnl": total_pnl / n,
                "best": max(float(t.pnl_usdt) for t in trades),
                "worst": min(float(t.pnl_usdt) for t in trades),
                "avg_hold_hrs": sum(t.holding_hours for t in trades) / n,
                "exit_reasons": exit_reasons,
                "return_pct": float((result.final_equity / INITIAL_CAPITAL - 1) * 100),
            })

    # Print results by strategy
    for strat_key, strat_name in STRATEGIES.items():
        strat_results = [r for r in results if r["strat_key"] == strat_key]
        strat_results.sort(key=lambda x: x["total_pnl"], reverse=True)
        total_pnl = sum(r["total_pnl"] for r in strat_results)
        total_trades = sum(r["trades"] for r in strat_results)

        print(f"\n{'=' * 110}")
        print(f"  {strat_name.upper()} (Total PnL: ${total_pnl:+.2f}, Trades: {total_trades})")
        print(f"{'=' * 110}")
        print(f"  {'Token':<10} {'Trades':>7} {'Wins':>5} {'Losses':>7} {'Win%':>6} {'Total PnL':>11} {'Avg PnL':>9} {'Best':>9} {'Worst':>9} {'Avg Hold':>9}  Exit Reasons")
        print(f"  {'─' * 105}")

        for r in strat_results:
            if r["trades"] == 0:
                print(f"  {r['symbol']:<10} {'—no trades—':>30}")
                continue
            exits_str = ", ".join(f"{k}:{v}" for k, v in sorted(r["exit_reasons"].items()))
            sign = "+" if r["total_pnl"] > 0 else ""
            print(
                f"  {r['symbol']:<10} {r['trades']:>7} {r['wins']:>5} {r['losses']:>7} "
                f"{r['win_rate']:>5.1f}% {sign}{r['total_pnl']:>10.2f} "
                f"{r['avg_pnl']:>9.2f} {r['best']:>9.2f} {r['worst']:>9.2f} "
                f"{r['avg_hold_hrs']:>8.1f}h  {exits_str}"
            )

    # Recommendations
    print(f"\n{'=' * 110}")
    print("  BEST TOKEN PER STRATEGY (2026 YTD)")
    print(f"{'=' * 110}")
    for strat_key, strat_name in STRATEGIES.items():
        strat_results = [r for r in results if r["strat_key"] == strat_key and r["trades"] > 0]
        if not strat_results:
            continue
        strat_results.sort(key=lambda x: x["total_pnl"], reverse=True)
        best = strat_results[0]
        profitable = [r for r in strat_results if r["total_pnl"] > 0]
        losers = [r for r in strat_results if r["total_pnl"] < 0]

        print(f"\n  {strat_name}:")
        print(f"    Best:  {best['symbol']} (${best['total_pnl']:+.2f}, {best['win_rate']:.0f}% WR, {best['trades']} trades)")
        if profitable:
            keep_parts = [r["symbol"] + f" ${r['total_pnl']:+.2f}" for r in profitable]
            print(f"    Keep:  {', '.join(keep_parts)}")
        if losers:
            drop_parts = [r["symbol"] + f" ${r['total_pnl']:+.2f}" for r in losers]
            print(f"    Drop:  {', '.join(drop_parts)}")


if __name__ == "__main__":
    main()
