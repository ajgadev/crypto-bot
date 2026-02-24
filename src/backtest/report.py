"""Backtest metrics computation and CSV export."""

from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from decimal import Decimal

from src.backtest.engine import BacktestResult


@dataclass
class BacktestMetrics:
    """Computed backtest performance metrics."""

    total_return_pct: float
    win_rate_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    total_trades: int
    avg_holding_hours: float


def generate_report(result: BacktestResult, output_dir: str = "data") -> BacktestMetrics:
    """Compute metrics and export trades to CSV."""
    trades = result.trades
    total_trades = len(trades)

    # Total return
    if result.initial_capital > 0:
        total_return = float(
            (result.final_equity / result.initial_capital - Decimal("1")) * 100
        )
    else:
        total_return = 0.0

    # Win rate
    wins = sum(1 for t in trades if t.pnl_usdt > 0)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

    # Max drawdown from equity curve
    max_dd = 0.0
    if result.equity_curve:
        peak = float(result.equity_curve[0])
        for eq in result.equity_curve:
            eq_f = float(eq)
            if eq_f > peak:
                peak = eq_f
            dd = (peak - eq_f) / peak * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

    # Sharpe ratio (annualized, hourly returns, rf=0)
    sharpe = 0.0
    if len(result.equity_curve) > 1:
        returns: list[float] = []
        for j in range(1, len(result.equity_curve)):
            prev = float(result.equity_curve[j - 1])
            curr = float(result.equity_curve[j])
            if prev > 0:
                returns.append((curr - prev) / prev)
        if returns:
            mean_r = sum(returns) / len(returns)
            variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
            std_r = math.sqrt(variance) if variance > 0 else 0.0
            if std_r > 0:
                sharpe = (mean_r / std_r) * math.sqrt(8760)  # annualize hourly

    # Avg holding hours
    avg_hours = (
        sum(t.holding_hours for t in trades) / total_trades if total_trades > 0 else 0.0
    )

    metrics = BacktestMetrics(
        total_return_pct=round(total_return, 2),
        win_rate_pct=round(win_rate, 2),
        max_drawdown_pct=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 4),
        total_trades=total_trades,
        avg_holding_hours=round(avg_hours, 1),
    )

    # Print summary
    print("\n=== BACKTEST REPORT ===")
    print(f"Total Return:      {metrics.total_return_pct:.2f}%")
    print(f"Win Rate:          {metrics.win_rate_pct:.2f}%")
    print(f"Max Drawdown:      {metrics.max_drawdown_pct:.2f}%")
    print(f"Sharpe Ratio:      {metrics.sharpe_ratio:.4f}")
    print(f"Total Trades:      {metrics.total_trades}")
    print(f"Avg Holding Hours: {metrics.avg_holding_hours:.1f}")
    print(f"Initial Capital:   {result.initial_capital}")
    print(f"Final Equity:      {result.final_equity:.2f}")
    print("========================\n")

    # Export trades CSV
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "backtest_trades.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "symbol", "side", "qty", "entry_price",
            "exit_price", "pnl_usdt", "pnl_pct", "exit_reason", "holding_hours",
        ])
        for t in trades:
            writer.writerow([
                t.entry_time, t.symbol, "BUY/SELL", str(t.quantity),
                str(t.entry_price), str(t.exit_price), str(t.pnl_usdt),
                str(t.pnl_pct), t.exit_reason, t.holding_hours,
            ])

    print(f"Trades exported to {csv_path}")
    return metrics
