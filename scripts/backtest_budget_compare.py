"""Compare budget allocation configs: current vs proposed (70/15/15 to MOM)."""

from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.backtest.data_loader import load_csv
from src.backtest.engine import run_backtest
from src.config.settings import Settings

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
INITIAL_CAPITAL = Decimal("1100")  # Match real capital

SYMBOL_FILES = {
    "BTCUSDC": "btcusdc_2026_1h.csv",
    "ETHUSDC": "ethusdc_2026_1h.csv",
    "BNBUSDC": "bnbusdc_2026_1h.csv",
    "SOLUSDC": "solusdc_2026_1h.csv",
    "XRPUSDC": "xrpusdc_2026_1h.csv",
}

CONFIGS = [
    {
        "label": "Budget 60/20/20 (per-strat symbols)",
        "settings": {
            "budget_allocation_enabled": True,
            "mr_budget_pct": Decimal("0.20"),
            "tf_budget_pct": Decimal("0.20"),
            "mom_budget_pct": Decimal("0.60"),
            "strategy_order": "mr,mom,tf",
        },
    },
    {
        "label": "No budget, order=mr,mom,tf (per-strat symbols)",
        "settings": {
            "budget_allocation_enabled": False,
            "strategy_order": "mr,mom,tf",
        },
    },
    {
        "label": "No budget, order=mom,mr,tf (per-strat symbols)",
        "settings": {
            "budget_allocation_enabled": False,
            "strategy_order": "mom,mr,tf",
        },
    },
    {
        "label": "Budget 30/40/30 old symbols (baseline)",
        "settings": {
            "budget_allocation_enabled": True,
            "mr_budget_pct": Decimal("0.30"),
            "tf_budget_pct": Decimal("0.40"),
            "mom_budget_pct": Decimal("0.30"),
            "strategy_order": "mr,mom,tf",
            # Override: old global symbols, no per-strategy filtering
            "mean_reversion_symbols": "",
            "trend_follow_symbols": "",
            "momentum_symbols": "ETHUSDC,SOLUSDC,BNBUSDC,BTCUSDC,XRPUSDC",
            "symbols": "BTCUSDC,ETHUSDC,BNBUSDC,SOLUSDC,XRPUSDC",
        },
    },
]

# Per-strategy symbols based on backtest winners (no XRP anywhere, BTC out of MOM)
BASE_SETTINGS = dict(
    symbols="BTCUSDC,ETHUSDC,BNBUSDC,SOLUSDC",
    binance_api_key="test",
    binance_api_secret="test",
    mean_reversion_enabled=True,
    mean_reversion_symbols="BTCUSDC,ETHUSDC,BNBUSDC,SOLUSDC",
    trend_follow_enabled=True,
    trend_follow_symbols="BTCUSDC,ETHUSDC,BNBUSDC,SOLUSDC",
    momentum_enabled=True,
    momentum_symbols="ETHUSDC,SOLUSDC,BNBUSDC",
    max_open_trades=2,
    trend_follow_max_trades=2,
    momentum_max_trades=2,
    defensive_mode_enabled=False,
    mean_reversion_regime_adaptive=False,
)


def make_settings(config: dict) -> Settings:
    merged = {**BASE_SETTINGS, **config}
    return Settings(**merged)


def analyze(result, label):
    trades = result.trades
    n = len(trades)
    total_pnl = sum(float(t.pnl_usdt) for t in trades)
    ret_pct = float((result.final_equity / result.initial_capital - 1) * 100)

    # Max drawdown
    max_dd = 0.0
    if result.equity_curve:
        peak = float(result.equity_curve[0])
        for eq in result.equity_curve:
            eq_f = float(eq)
            if eq_f > peak:
                peak = eq_f
            dd = (peak - eq_f) / peak * 100
            if dd > max_dd:
                max_dd = dd

    wins = sum(1 for t in trades if t.pnl_usdt > 0)

    print(f"\n{'=' * 90}")
    print(f"  {label}")
    print(f"{'=' * 90}")
    print(f"  Final Equity: ${float(result.final_equity):.2f} | Return: {ret_pct:+.2f}% | Max DD: {max_dd:.2f}%")
    print(f"  Trades: {n} | Wins: {wins} | Win Rate: {wins/n*100:.1f}% | Total PnL: ${total_pnl:+.2f}")

    # Per strategy
    strats = {"mean_reversion": "MR", "trend_follow": "TF", "momentum": "MOM"}
    print(f"\n  {'Strat':<6} {'Trades':>7} {'Wins':>5} {'Win%':>6} {'PnL':>10} {'Avg PnL':>9}")
    print(f"  {'─' * 48}")
    for strat_key, strat_label in strats.items():
        st = [t for t in trades if t.strategy == strat_key]
        if not st:
            print(f"  {strat_label:<6} {'—':>7}")
            continue
        s_wins = sum(1 for t in st if t.pnl_usdt > 0)
        s_pnl = sum(float(t.pnl_usdt) for t in st)
        s_avg = s_pnl / len(st)
        sign = "+" if s_pnl > 0 else ""
        print(f"  {strat_label:<6} {len(st):>7} {s_wins:>5} {s_wins/len(st)*100:>5.1f}% {sign}{s_pnl:>9.2f} {s_avg:>9.2f}")

    # Per strategy x symbol
    print(f"\n  {'Strat':<6} {'Token':<10} {'Trades':>7} {'Win%':>6} {'PnL':>10}")
    print(f"  {'─' * 44}")
    for strat_key, strat_label in strats.items():
        st = [t for t in trades if t.strategy == strat_key]
        if not st:
            continue
        symbols = sorted(set(t.symbol for t in st))
        for sym in symbols:
            sym_trades = [t for t in st if t.symbol == sym]
            s_wins = sum(1 for t in sym_trades if t.pnl_usdt > 0)
            s_pnl = sum(float(t.pnl_usdt) for t in sym_trades)
            sign = "+" if s_pnl > 0 else ""
            print(f"  {strat_label:<6} {sym:<10} {len(sym_trades):>7} {s_wins/len(sym_trades)*100:>5.1f}% {sign}{s_pnl:>9.2f}")

    return {"label": label, "final_equity": float(result.final_equity), "return_pct": ret_pct,
            "max_dd": max_dd, "trades": n, "pnl": total_pnl, "win_rate": wins/n*100 if n else 0}


def main():
    print("Loading 2026 data...")
    klines = {}
    for symbol, fname in SYMBOL_FILES.items():
        path = os.path.join(DATA_DIR, fname)
        if os.path.exists(path):
            klines[symbol] = load_csv(path)
            print(f"  {symbol}: {len(klines[symbol])} candles")

    summaries = []
    for cfg in CONFIGS:
        settings = make_settings(cfg["settings"])
        result = run_backtest(klines, INITIAL_CAPITAL, settings)
        summaries.append(analyze(result, cfg["label"]))

    # Comparison table
    print(f"\n{'=' * 90}")
    print(f"  COMPARISON SUMMARY")
    print(f"{'=' * 90}")
    print(f"  {'Config':<50} {'Equity':>10} {'Return':>8} {'Max DD':>8} {'Trades':>7} {'PnL':>10} {'WR%':>6}")
    print(f"  {'─' * 95}")
    for s in summaries:
        print(f"  {s['label']:<50} ${s['final_equity']:>8.2f} {s['return_pct']:>+7.2f}% {s['max_dd']:>7.2f}% {s['trades']:>7} ${s['pnl']:>+8.2f} {s['win_rate']:>5.1f}%")


if __name__ == "__main__":
    main()
