"""Compare backtest: shared budget (FCFS) vs per-strategy budget allocation."""

from __future__ import annotations

import os
import sys
from decimal import Decimal

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.backtest.data_loader import load_csv
from src.backtest.engine import BacktestResult, run_backtest
from src.config.settings import Settings

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
INITIAL_CAPITAL = Decimal("1100")  # ~match live account

# 2026 data files
SYMBOL_FILES = {
    "BTCUSDC": "btcusdc_2026_1h.csv",
    "ETHUSDC": "ethusdc_2026_1h.csv",
    "BNBUSDC": "bnbusdc_2026_1h.csv",
    "SOLUSDC": "solusdc_2026_1h.csv",
    "XRPUSDC": "xrpusdc_2026_1h.csv",
}


def load_data() -> dict[str, list]:
    klines = {}
    for symbol, fname in SYMBOL_FILES.items():
        path = os.path.join(DATA_DIR, fname)
        if os.path.exists(path):
            klines[symbol] = load_csv(path)
            print(f"  Loaded {symbol}: {len(klines[symbol])} candles")
        else:
            print(f"  WARNING: {path} not found, skipping {symbol}")
    return klines


def base_settings() -> dict:
    """Settings matching the live bot config from logs."""
    return {
        "symbols": "BTCUSDC,ETHUSDC,BNBUSDC,SOLUSDC,XRPUSDC",
        "mean_reversion_enabled": True,
        "max_open_trades": 2,
        "take_profit_pct": Decimal("0.03"),
        "stop_loss_pct": Decimal("0.02"),
        "trend_follow_enabled": True,
        "trend_follow_max_trades": 2,
        "trend_follow_trailing_stop_pct": Decimal("0.15"),
        "momentum_enabled": True,
        "momentum_max_trades": 2,
        "momentum_take_profit_pct": Decimal("0.025"),
        "momentum_stop_loss_pct": Decimal("0.022"),
        "reserve_pct": Decimal("0.10"),
        "risk_pct": Decimal("0.02"),
        "defensive_mode_enabled": False,
        "mean_reversion_regime_adaptive": False,
        "binance_api_key": "test",
        "binance_api_secret": "test",
    }


def print_result(label: str, result: BacktestResult) -> None:
    trades = result.trades
    total = len(trades)
    wins = sum(1 for t in trades if t.pnl_usdt > 0)
    total_pnl = sum(t.pnl_usdt for t in trades)
    ret_pct = (result.final_equity / result.initial_capital - 1) * 100

    # Max drawdown
    max_dd = Decimal("0")
    if result.equity_curve:
        peak = result.equity_curve[0]
        for eq in result.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100 if peak > 0 else Decimal("0")
            if dd > max_dd:
                max_dd = dd

    print(f"\n{'=' * 50}")
    print(f"  {label}")
    print(f"{'=' * 50}")
    print(f"  Return:       {ret_pct:>8.2f}%")
    print(f"  Final Equity: {result.final_equity:>10.2f} USDT")
    print(f"  Total PnL:    {total_pnl:>10.2f} USDT")
    print(f"  Max Drawdown: {max_dd:>8.2f}%")
    print(f"  Total Trades: {total:>5}")
    print(f"  Win Rate:     {(wins / total * 100) if total else 0:>8.2f}%")

    # Per-strategy breakdown
    for strat_name, strat_label in [
        ("mean_reversion", "MR"),
        ("trend_follow", "TF"),
        ("momentum", "MOM"),
    ]:
        st = [t for t in trades if t.strategy == strat_name]
        if st:
            s_wins = sum(1 for t in st if t.pnl_usdt > 0)
            s_pnl = sum(t.pnl_usdt for t in st)
            s_wr = s_wins / len(st) * 100
            print(f"  {strat_label:>5}: {len(st):>3} trades, PnL {s_pnl:>8.2f}, WR {s_wr:>5.1f}%")
        else:
            print(f"  {strat_label:>5}: no trades")


def main() -> None:
    print("Loading 2026 data...")
    klines = load_data()
    if not klines:
        print("No data found!")
        return

    # ── Run 1: Shared budget (FCFS) — current behavior ──
    print("\n>>> Running backtest: SHARED BUDGET (first-come-first-served)...")
    env_shared = base_settings()
    env_shared["budget_allocation_enabled"] = False
    settings_shared = Settings(**env_shared)
    result_shared = run_backtest(klines, INITIAL_CAPITAL, settings_shared)

    # ── Run 2: FCFS but MOM before TF (MR still first) ──
    print("\n>>> Running backtest: SHARED BUDGET (mr,mom,tf)...")
    env_mom_before_tf = base_settings()
    env_mom_before_tf["budget_allocation_enabled"] = False
    env_mom_before_tf["strategy_order"] = "mr,mom,tf"
    settings_mom_before_tf = Settings(**env_mom_before_tf)
    result_mom_before_tf = run_backtest(klines, INITIAL_CAPITAL, settings_mom_before_tf)

    # ── Run 3: FCFS with MOM truly first ──
    print("\n>>> Running backtest: SHARED BUDGET (mom,mr,tf)...")
    env_mom_first = base_settings()
    env_mom_first["budget_allocation_enabled"] = False
    env_mom_first["strategy_order"] = "mom,mr,tf"
    settings_mom_first = Settings(**env_mom_first)
    result_mom_first = run_backtest(klines, INITIAL_CAPITAL, settings_mom_first)

    # ── Run 4: Per-strategy budget allocation (40/30/30) ──
    print("\n>>> Running backtest: PER-STRATEGY BUDGET (TF 40%, MR 30%, MOM 30%)...")
    env_alloc = base_settings()
    env_alloc["budget_allocation_enabled"] = True
    env_alloc["mr_budget_pct"] = Decimal("0.30")
    env_alloc["tf_budget_pct"] = Decimal("0.40")
    env_alloc["mom_budget_pct"] = Decimal("0.30")
    settings_alloc = Settings(**env_alloc)
    result_alloc = run_backtest(klines, INITIAL_CAPITAL, settings_alloc)

    # ── Run 3: Per-strategy budget allocation (30/40/30) — more to MOM ──
    print("\n>>> Running backtest: PER-STRATEGY BUDGET (TF 30%, MR 30%, MOM 40%)...")
    env_alloc2 = base_settings()
    env_alloc2["budget_allocation_enabled"] = True
    env_alloc2["mr_budget_pct"] = Decimal("0.30")
    env_alloc2["tf_budget_pct"] = Decimal("0.30")
    env_alloc2["mom_budget_pct"] = Decimal("0.40")
    settings_alloc2 = Settings(**env_alloc2)
    result_alloc2 = run_backtest(klines, INITIAL_CAPITAL, settings_alloc2)

    # ── Run 4: Equal split (33/34/33) ──
    print("\n>>> Running backtest: PER-STRATEGY BUDGET (equal 33/34/33)...")
    env_equal = base_settings()
    env_equal["budget_allocation_enabled"] = True
    env_equal["mr_budget_pct"] = Decimal("0.33")
    env_equal["tf_budget_pct"] = Decimal("0.34")
    env_equal["mom_budget_pct"] = Decimal("0.33")
    settings_equal = Settings(**env_equal)
    result_equal = run_backtest(klines, INITIAL_CAPITAL, settings_equal)

    # ── Print comparison ──
    print("\n" + "=" * 60)
    print("  COMPARISON: 2026 DATA (all strategies enabled)")
    print("=" * 60)

    print_result("SHARED BUDGET (FCFS — current)", result_shared)
    print_result("SHARED BUDGET (mr,mom,tf)", result_mom_before_tf)
    print_result("SHARED BUDGET (mom,mr,tf)", result_mom_first)
    print_result("PER-STRATEGY: TF 40% / MR 30% / MOM 30%", result_alloc)
    print_result("PER-STRATEGY: TF 30% / MR 30% / MOM 40%", result_alloc2)
    print_result("PER-STRATEGY: EQUAL 33% / 34% / 33%", result_equal)

    # Summary table
    configs = [
        ("Shared (FCFS)", result_shared),
        ("FCFS mr,mom,tf", result_mom_before_tf),
        ("FCFS mom,mr,tf", result_mom_first),
        ("TF40/MR30/MOM30", result_alloc),
        ("TF30/MR30/MOM40", result_alloc2),
        ("Equal 33/34/33", result_equal),
    ]
    print(f"\n{'Config':<20} {'Return':>8} {'Trades':>7} {'WinRate':>8} {'MOM trades':>10}")
    print("-" * 55)
    for label, r in configs:
        ret = (r.final_equity / r.initial_capital - 1) * 100
        total = len(r.trades)
        wins = sum(1 for t in r.trades if t.pnl_usdt > 0)
        wr = (wins / total * 100) if total else 0
        mom_t = sum(1 for t in r.trades if t.strategy == "momentum")
        print(f"{label:<20} {ret:>7.2f}% {total:>7} {wr:>7.1f}% {mom_t:>10}")


if __name__ == "__main__":
    main()
