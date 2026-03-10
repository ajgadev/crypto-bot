"""Pure backtesting engine using the exact same signal functions as live."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from src.binance.types import Kline
from src.config.settings import Settings
from src.indicators.ema import compute_ema
from src.indicators.percent_change import compute_pct_change_24h
from src.indicators.rsi import compute_rsi
from src.indicators.volume import compute_volume_sma
from src.strategy.signals import (
    Indicators,
    check_defensive_mode,
    check_entry_signal,
    check_exit_signal,
    check_trend_follow_entry,
    check_trend_follow_exit,
)


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
    strategy: str = "mean_reversion"


@dataclass
class OpenPosition:
    """Tracked open position during backtest."""

    symbol: str
    entry_time: int
    entry_price: Decimal
    quantity: Decimal
    strategy: str = "mean_reversion"
    highest_price: Decimal = Decimal("0")


@dataclass
class BacktestResult:
    """Aggregated backtest output."""

    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[Decimal] = field(default_factory=list)
    initial_capital: Decimal = Decimal("0")
    final_equity: Decimal = Decimal("0")


def _build_mr_indicators(closes: list[Decimal], trend_ema_period: int = 50) -> Indicators:
    """Build Indicators for mean-reversion (fixed EMA 9/21 + trend EMA)."""
    rsi = compute_rsi(closes[-50:], 14)
    ema9 = compute_ema(closes[-50:], 9)
    ema21 = compute_ema(closes[-50:], 21)
    pct_change = compute_pct_change_24h(closes) if len(closes) >= 25 else Decimal("0")
    ema_trend = (
        compute_ema(closes[-(trend_ema_period + 10):], trend_ema_period)
        if len(closes) >= trend_ema_period
        else None
    )
    return Indicators(
        rsi=rsi,
        ema_short=ema9,
        ema_long=ema21,
        pct_change_24h=pct_change,
        last_close=closes[-1],
        ema_trend=ema_trend,
    )


def _build_tf_indicators(
    closes: list[Decimal],
    volumes: list[Decimal],
    settings: Settings,
) -> Indicators:
    """Build Indicators for trend-follow (configurable EMA periods + history)."""
    ema_short_period = settings.trend_follow_ema_short
    ema_long_period = settings.trend_follow_ema_long

    rsi = compute_rsi(closes[-50:], 14)
    ema_short = compute_ema(closes[-80:], ema_short_period)
    ema_long = compute_ema(closes[-80:], ema_long_period)
    pct_change = compute_pct_change_24h(closes) if len(closes) >= 25 else Decimal("0")

    # EMA history for crossover detection (last N candles)
    crossover_window = settings.trend_follow_crossover_window
    ema_short_history: list[Decimal] = []
    ema_long_history: list[Decimal] = []
    for offset in range(crossover_window, 0, -1):
        hist_closes = closes[:-offset]
        if len(hist_closes) >= ema_long_period:
            ema_short_history.append(compute_ema(hist_closes[-80:], ema_short_period))
            ema_long_history.append(compute_ema(hist_closes[-80:], ema_long_period))

    # Volume
    vol_period = settings.trend_follow_volume_period
    current_volume = volumes[-1] if volumes else None
    avg_volume = (
        compute_volume_sma(volumes, vol_period)
        if len(volumes) >= vol_period
        else None
    )

    return Indicators(
        rsi=rsi,
        ema_short=ema_short,
        ema_long=ema_long,
        pct_change_24h=pct_change,
        last_close=closes[-1],
        ema_short_history=ema_short_history or None,
        ema_long_history=ema_long_history or None,
        current_volume=current_volume,
        avg_volume=avg_volume,
    )


def run_backtest(
    klines_by_symbol: dict[str, list[Kline]],
    initial_capital: Decimal = Decimal("10000"),
    settings: Settings | None = None,
    fee_pct: Decimal = Decimal("0.001"),
) -> BacktestResult:
    """Run backtest using the same signal functions as live trading."""
    if settings is None:
        settings = Settings()

    result = BacktestResult(initial_capital=initial_capital)
    cash = initial_capital
    open_positions: list[OpenPosition] = []
    mr_max = settings.max_open_trades if settings.mean_reversion_enabled else 0
    tf_max = settings.trend_follow_max_trades if settings.trend_follow_enabled else 0
    warmup = 50  # candles needed for indicators

    symbols = list(klines_by_symbol.keys())
    if not symbols:
        return result

    ref_len = min(len(klines_by_symbol[s]) for s in symbols)

    # Defensive mode: precompute reference closes if enabled
    defensive_ref = settings.defensive_mode_reference
    has_defensive_ref = (
        settings.defensive_mode_enabled and defensive_ref in klines_by_symbol
    )

    # Regime-adaptive MR
    regime_ref = settings.mean_reversion_regime_reference
    has_regime_ref = (
        settings.mean_reversion_regime_adaptive and regime_ref in klines_by_symbol
    )

    for i in range(warmup, ref_len):
        # ── Regime-adaptive MR settings ──
        mr_settings = settings
        if has_regime_ref:
            regime_closes = [k.close for k in klines_by_symbol[regime_ref][: i + 1]]
            regime_ema_period = settings.mean_reversion_regime_ema
            if len(regime_closes) >= regime_ema_period:
                regime_ema_val = compute_ema(
                    regime_closes[-(regime_ema_period + 10):], regime_ema_period
                )
                if regime_closes[-1] < regime_ema_val:
                    mr_settings = settings.with_bear_mr_params()

        # ── Defensive mode check ──
        is_bear = False
        if has_defensive_ref:
            ref_closes = [k.close for k in klines_by_symbol[defensive_ref][: i + 1]]
            is_bear = check_defensive_mode(ref_closes, settings)

        # ── If bear: force-exit all open positions ──
        if is_bear:
            for pos in list(open_positions):
                sym_klines = klines_by_symbol[pos.symbol]
                current_price = sym_klines[i].close
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
                        exit_reason="DEFENSIVE_EXIT",
                        holding_hours=holding_hours,
                        strategy=pos.strategy,
                    )
                )
                cash += proceeds
                open_positions.remove(pos)

            # Track equity and skip entries
            positions_value = sum(
                p.quantity * klines_by_symbol[p.symbol][min(i, len(klines_by_symbol[p.symbol]) - 1)].close
                for p in open_positions
            )
            result.equity_curve.append(cash + positions_value)
            continue

        # ── Check exits first ──
        for pos in list(open_positions):
            sym_klines = klines_by_symbol[pos.symbol]
            current_price = sym_klines[i].close
            closes = [k.close for k in sym_klines[: i + 1]]
            volumes = [k.volume for k in sym_klines[: i + 1]]

            exit_reason = ""

            if pos.strategy == "mean_reversion":
                rsi = compute_rsi(closes[-50:], 14)
                exit_sig = check_exit_signal(
                    pos.entry_price, current_price, rsi, mr_settings
                )
                if exit_sig.should_exit:
                    exit_reason = exit_sig.reason

            elif pos.strategy == "trend_follow":
                # Update highest price
                if current_price > pos.highest_price:
                    pos.highest_price = current_price

                indicators = _build_tf_indicators(closes, volumes, settings)
                exit_sig = check_trend_follow_exit(
                    entry_price=pos.entry_price,
                    highest_price=pos.highest_price,
                    current_price=current_price,
                    indicators=indicators,
                    settings=settings,
                )
                if exit_sig.should_exit:
                    exit_reason = exit_sig.reason

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
                        strategy=pos.strategy,
                    )
                )
                cash += proceeds
                open_positions.remove(pos)

        # ── Check entries ──
        mr_count = sum(1 for p in open_positions if p.strategy == "mean_reversion")
        tf_count = sum(1 for p in open_positions if p.strategy == "trend_follow")

        for symbol in symbols:
            sym_klines = klines_by_symbol[symbol]
            if i >= len(sym_klines):
                continue

            closes = [k.close for k in sym_klines[: i + 1]]
            if len(closes) < warmup:
                continue

            volumes = [k.volume for k in sym_klines[: i + 1]]
            current_price = closes[-1]

            # Shared budget calc
            positions_value = sum(
                p.quantity * klines_by_symbol[p.symbol][i].close for p in open_positions
            )
            equity = cash + positions_value
            reserve = max(Decimal("20"), equity * settings.reserve_pct)
            tradable = max(Decimal("0"), cash - reserve)

            # ── Mean-reversion entry ──
            if mr_max > 0:
                mr_indicators = _build_mr_indicators(closes, mr_settings.mean_reversion_trend_ema)
                mr_has_open = any(
                    p.symbol == symbol and p.strategy == "mean_reversion" for p in open_positions
                )
                mr_slots = mr_max - mr_count

                entry_sig = check_entry_signal(
                    mr_indicators, mr_has_open, mr_slots, tradable, mr_settings
                )

                if entry_sig.should_enter:
                    qty, cost = _backtest_position_size(
                        cash, positions_value, equity, reserve, tradable, mr_slots,
                        current_price, fee_pct, mr_settings,
                    )
                    if qty and cash - cost >= reserve:
                        cash -= cost
                        open_positions.append(
                            OpenPosition(
                                symbol=symbol,
                                entry_time=sym_klines[i].open_time,
                                entry_price=current_price,
                                quantity=qty,
                                strategy="mean_reversion",
                            )
                        )
                        mr_count += 1
                        # Recompute tradable after MR entry
                        positions_value = sum(
                            p.quantity * klines_by_symbol[p.symbol][i].close
                            for p in open_positions
                        )
                        equity = cash + positions_value
                        reserve = max(Decimal("20"), equity * settings.reserve_pct)
                        tradable = max(Decimal("0"), cash - reserve)

            # ── Trend-follow entry ──
            if tf_max > 0 and i > 0:
                tf_indicators = _build_tf_indicators(closes, volumes, settings)
                tf_has_open = any(
                    p.symbol == symbol and p.strategy == "trend_follow" for p in open_positions
                )
                tf_slots = tf_max - tf_count

                entry_sig = check_trend_follow_entry(
                    tf_indicators, tf_has_open, tf_slots, tradable, settings
                )

                if entry_sig.should_enter:
                    qty, cost = _backtest_position_size(
                        cash, positions_value, equity, reserve, tradable, tf_slots,
                        current_price, fee_pct, settings,
                    )
                    if qty and cash - cost >= reserve:
                        cash -= cost
                        open_positions.append(
                            OpenPosition(
                                symbol=symbol,
                                entry_time=sym_klines[i].open_time,
                                entry_price=current_price,
                                quantity=qty,
                                strategy="trend_follow",
                                highest_price=current_price,
                            )
                        )
                        tf_count += 1

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
                strategy=pos.strategy,
            )
        )
        cash += proceeds

    result.final_equity = cash
    return result


def _backtest_position_size(
    cash: Decimal,
    positions_value: Decimal,
    equity: Decimal,
    reserve: Decimal,
    tradable: Decimal,
    slots: int,
    current_price: Decimal,
    fee_pct: Decimal,
    settings: Settings,
) -> tuple[Decimal | None, Decimal]:
    """Simplified position sizing for backtest. Returns (qty, cost) or (None, 0)."""
    if tradable <= 0 or slots <= 0:
        return None, Decimal("0")

    per_trade_cap = tradable / Decimal(str(slots))
    risk_budget = equity * settings.risk_pct
    notional_by_risk = (
        risk_budget / settings.stop_loss_pct if settings.stop_loss_pct > 0 else per_trade_cap
    )
    order_notional = min(per_trade_cap, notional_by_risk) * (Decimal("1") - fee_pct)

    qty = order_notional / current_price
    cost = qty * current_price

    if cost < Decimal("10"):
        return None, Decimal("0")
    if cash - cost < reserve:
        return None, Decimal("0")

    return qty, cost
