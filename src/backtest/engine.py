"""Pure backtesting engine simulating the exact same strategy as live."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from src.binance.types import Kline
from src.config.settings import Settings
from src.indicators.ema import compute_ema
from src.indicators.percent_change import compute_pct_change_24h
from src.indicators.rsi import compute_rsi


@dataclass
class BacktestTrade:
    """A completed backtest trade."""

    symbol: str
    entry_time: int
    entry_price: Decimal
    exit_time: int
    exit_price: Decimal
    quantity: Decimal
    pnl_usdt: Decimal
    pnl_pct: Decimal
    exit_reason: str
    holding_hours: int


@dataclass
class OpenPosition:
    """Tracked open position during backtest."""

    symbol: str
    entry_time: int
    entry_price: Decimal
    quantity: Decimal


@dataclass
class BacktestResult:
    """Aggregated backtest output."""

    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[Decimal] = field(default_factory=list)
    initial_capital: Decimal = Decimal("0")
    final_equity: Decimal = Decimal("0")


def run_backtest(
    klines_by_symbol: dict[str, list[Kline]],
    initial_capital: Decimal = Decimal("10000"),
    settings: Settings | None = None,
    fee_pct: Decimal = Decimal("0.001"),
) -> BacktestResult:
    """Run backtest using the same strategy rules as live trading."""
    if settings is None:
        settings = Settings()

    result = BacktestResult(initial_capital=initial_capital)
    cash = initial_capital
    open_positions: list[OpenPosition] = []
    max_open = settings.max_open_trades
    warmup = 50  # candles needed for indicators

    # Merge all klines into time-ordered steps
    # For simplicity, iterate symbol by symbol on aligned candles
    # Get the symbol with most candles as reference timeline
    symbols = list(klines_by_symbol.keys())
    if not symbols:
        return result

    # Use first symbol's timeline length as reference
    ref_len = min(len(klines_by_symbol[s]) for s in symbols)

    for i in range(warmup, ref_len):
        # Check exits first
        for pos in list(open_positions):
            sym_klines = klines_by_symbol[pos.symbol]
            current_price = sym_klines[i].close
            closes = [k.close for k in sym_klines[: i + 1]]

            rsi = compute_rsi(closes[-50:], 14)

            # Check TP/SL/RSI exit
            tp_price = pos.entry_price * settings.tp_multiplier
            sl_price = pos.entry_price * settings.sl_multiplier

            exit_reason = ""
            if current_price >= tp_price:
                exit_reason = "TP"
            elif current_price <= sl_price:
                exit_reason = "SL"
            elif rsi > Decimal("65"):
                exit_reason = "RSI_EXIT"

            if exit_reason:
                proceeds = pos.quantity * current_price * (Decimal("1") - fee_pct)
                pnl = proceeds - (pos.quantity * pos.entry_price)
                pnl_pct = (current_price / pos.entry_price - Decimal("1")) * 100
                holding_hours = (sym_klines[i].open_time - pos.entry_time) // (3600 * 1000)

                result.trades.append(
                    BacktestTrade(
                        symbol=pos.symbol,
                        entry_time=pos.entry_time,
                        entry_price=pos.entry_price,
                        exit_time=sym_klines[i].open_time,
                        exit_price=current_price,
                        quantity=pos.quantity,
                        pnl_usdt=pnl,
                        pnl_pct=pnl_pct,
                        exit_reason=exit_reason,
                        holding_hours=holding_hours,
                    )
                )
                cash += proceeds
                open_positions.remove(pos)

        # Check entries
        for symbol in symbols:
            sym_klines = klines_by_symbol[symbol]
            if i >= len(sym_klines):
                continue

            closes = [k.close for k in sym_klines[: i + 1]]
            if len(closes) < warmup:
                continue

            rsi = compute_rsi(closes[-50:], 14)
            ema9 = compute_ema(closes[-50:], 9)
            ema21 = compute_ema(closes[-50:], 21)

            pct_change = Decimal("0")
            if len(closes) >= 25:
                pct_change = compute_pct_change_24h(closes)

            current_price = closes[-1]

            # Entry conditions
            is_bullish = ema9 > ema21
            has_open = any(p.symbol == symbol for p in open_positions)
            slots = max_open - len(open_positions)

            if (
                is_bullish
                and pct_change <= Decimal("-0.03")
                and rsi < Decimal("35")
                and not has_open
                and slots > 0
            ):
                # Position sizing (simplified for backtest)
                positions_value = sum(
                    p.quantity * klines_by_symbol[p.symbol][i].close for p in open_positions
                )
                equity = cash + positions_value
                reserve = max(Decimal("20"), equity * settings.reserve_pct)
                tradable = max(Decimal("0"), cash - reserve)

                if tradable <= 0:
                    continue

                per_trade_cap = tradable / Decimal(str(slots))
                risk_budget = equity * settings.risk_pct
                notional_by_risk = (
                    risk_budget / settings.stop_loss_pct if settings.stop_loss_pct > 0 else per_trade_cap
                )
                order_notional = min(per_trade_cap, notional_by_risk) * (Decimal("1") - fee_pct)

                qty = order_notional / current_price
                cost = qty * current_price

                if cost < Decimal("10"):  # MIN_NOTIONAL proxy
                    continue
                if cash - cost < reserve:
                    continue

                cash -= cost
                open_positions.append(
                    OpenPosition(
                        symbol=symbol,
                        entry_time=sym_klines[i].open_time,
                        entry_price=current_price,
                        quantity=qty,
                    )
                )

        # Track equity
        positions_value = sum(
            p.quantity * klines_by_symbol[p.symbol][min(i, len(klines_by_symbol[p.symbol]) - 1)].close
            for p in open_positions
        )
        result.equity_curve.append(cash + positions_value)

    # Close any remaining open positions at last price
    for pos in open_positions:
        sym_klines = klines_by_symbol[pos.symbol]
        last_price = sym_klines[-1].close
        proceeds = pos.quantity * last_price * (Decimal("1") - fee_pct)
        pnl = proceeds - (pos.quantity * pos.entry_price)
        pnl_pct = (last_price / pos.entry_price - Decimal("1")) * 100
        holding_hours = (sym_klines[-1].open_time - pos.entry_time) // (3600 * 1000)

        result.trades.append(
            BacktestTrade(
                symbol=pos.symbol,
                entry_time=pos.entry_time,
                entry_price=pos.entry_price,
                exit_time=sym_klines[-1].open_time,
                exit_price=last_price,
                quantity=pos.quantity,
                pnl_usdt=pnl,
                pnl_pct=pnl_pct,
                exit_reason="END_OF_DATA",
                holding_hours=holding_hours,
            )
        )
        cash += proceeds

    result.final_equity = cash
    return result
