"""Entry and exit signal generation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from src.config.settings import Settings

logger = logging.getLogger("crypto_bot")


@dataclass
class Indicators:
    """Computed indicator values for a symbol."""

    rsi: Decimal
    ema_short: Decimal
    ema_long: Decimal
    pct_change_24h: Decimal
    last_close: Decimal
    ema_short_history: Optional[list[Decimal]] = None
    ema_long_history: Optional[list[Decimal]] = None
    ema_trend: Optional[Decimal] = None
    current_volume: Optional[Decimal] = None
    avg_volume: Optional[Decimal] = None


@dataclass
class EntrySignal:
    """Result of entry signal check."""

    should_enter: bool
    reason: str
    indicators: Indicators


@dataclass
class ExitSignal:
    """Result of exit signal check."""

    should_exit: bool
    reason: str  # TP, SL, RSI_EXIT, TRAILING_STOP, DEATH_CROSS, or empty


def check_defensive_mode(
    reference_closes: list[Decimal],
    settings: Settings,
) -> bool:
    """Return True (bear market) when reference symbol's close < EMA(defensive_mode_ema).

    Returns False (not bear) if defensive mode is disabled or not enough data.
    """
    if not settings.defensive_mode_enabled:
        return False

    ema_period = settings.defensive_mode_ema
    if len(reference_closes) < ema_period:
        return False

    from src.indicators.ema import compute_ema

    ema_value = compute_ema(reference_closes[-(ema_period + 10):], ema_period)
    return reference_closes[-1] < ema_value


def check_entry_signal(
    indicators: Indicators,
    has_open_trade: bool,
    slots_remaining: int,
    tradable_usdt: Decimal,
    settings: Settings | None = None,
) -> EntrySignal:
    """Evaluate all entry conditions. ALL must be true for a LONG entry."""
    rsi_max = settings.mean_reversion_rsi_max if settings else Decimal("50")
    pct_drop = settings.mean_reversion_pct_drop if settings else Decimal("-0.01")

    # Macro trend filter: price must be above trend EMA
    trend_filter_on = settings.mean_reversion_trend_filter if settings else True
    if trend_filter_on and indicators.ema_trend is not None and indicators.last_close <= indicators.ema_trend:
        return EntrySignal(
            False,
            f"Downtrend (close {indicators.last_close:.2f} <= EMA{settings.mean_reversion_trend_ema if settings else 50} {indicators.ema_trend:.2f})",
            indicators,
        )

    # Bullish bias: EMA(9) > EMA(21)
    is_bullish = indicators.ema_short > indicators.ema_long
    if not is_bullish:
        return EntrySignal(False, "Bearish bias (EMA9 <= EMA21)", indicators)

    # 24h drop threshold
    if indicators.pct_change_24h > pct_drop:
        return EntrySignal(
            False,
            f"24h change {indicators.pct_change_24h:.4f} > {pct_drop}",
            indicators,
        )

    # RSI threshold
    if indicators.rsi >= rsi_max:
        return EntrySignal(False, f"RSI {indicators.rsi:.2f} >= {rsi_max}", indicators)

    # No existing open trade
    if has_open_trade:
        return EntrySignal(False, "Already has open trade", indicators)

    # Slots available
    if slots_remaining <= 0:
        return EntrySignal(False, "No trade slots remaining", indicators)

    # Budget available
    if tradable_usdt <= 0:
        return EntrySignal(False, "No tradable budget", indicators)

    return EntrySignal(True, "All entry conditions met", indicators)


def check_exit_signal(
    entry_price: Decimal,
    current_price: Decimal,
    rsi: Decimal,
    settings: Settings,
) -> ExitSignal:
    """Check exit conditions for an open trade. ANY triggers exit."""
    # Take-profit
    tp_price = entry_price * settings.tp_multiplier
    if current_price >= tp_price:
        return ExitSignal(True, "TP")

    # Stop-loss
    sl_price = entry_price * settings.sl_multiplier
    if current_price <= sl_price:
        return ExitSignal(True, "SL")

    # RSI exit threshold
    if rsi > settings.mean_reversion_rsi_exit:
        return ExitSignal(True, "RSI_EXIT")

    return ExitSignal(False, "")


# ── Trend-follow signals ──


def check_trend_follow_entry(
    indicators: Indicators,
    has_open_trade: bool,
    slots_remaining: int,
    tradable_usdt: Decimal,
    settings: Settings,
) -> EntrySignal:
    """Evaluate trend-follow entry: EMA short crosses above EMA long (within window) + RSI + volume."""
    crossover_window = settings.trend_follow_crossover_window

    # Need EMA history for crossover detection
    short_hist = indicators.ema_short_history
    long_hist = indicators.ema_long_history
    if not short_hist or not long_hist:
        return EntrySignal(False, "No previous EMA data for crossover", indicators)

    # Current must be bullish
    if indicators.ema_short <= indicators.ema_long:
        return EntrySignal(False, "No fresh EMA crossover", indicators)

    # Scan back up to crossover_window candles for a bearish candle
    lookback = min(crossover_window, len(short_hist))
    recent_crossover = any(
        short_hist[-(j + 1)] <= long_hist[-(j + 1)]
        for j in range(lookback)
    )
    if not recent_crossover:
        return EntrySignal(False, "No fresh EMA crossover", indicators)

    # RSI in range [rsi_min, rsi_max]
    if indicators.rsi < settings.trend_follow_rsi_min:
        return EntrySignal(
            False,
            f"RSI {indicators.rsi:.2f} < {settings.trend_follow_rsi_min}",
            indicators,
        )
    if indicators.rsi > settings.trend_follow_rsi_max:
        return EntrySignal(
            False,
            f"RSI {indicators.rsi:.2f} > {settings.trend_follow_rsi_max}",
            indicators,
        )

    # Volume confirmation
    if indicators.current_volume is None or indicators.avg_volume is None:
        return EntrySignal(False, "No volume data", indicators)
    vol_threshold = indicators.avg_volume * settings.trend_follow_volume_multiplier
    if indicators.current_volume < vol_threshold:
        return EntrySignal(
            False,
            f"Volume {indicators.current_volume:.2f} < {vol_threshold:.2f}",
            indicators,
        )

    # No existing open trend_follow trade for this symbol
    if has_open_trade:
        return EntrySignal(False, "Already has open trend_follow trade", indicators)

    if slots_remaining <= 0:
        return EntrySignal(False, "No trend_follow slots remaining", indicators)

    if tradable_usdt <= 0:
        return EntrySignal(False, "No tradable budget", indicators)

    return EntrySignal(True, "Trend-follow entry: crossover + RSI + volume", indicators)


def check_trend_follow_exit(
    entry_price: Decimal,
    highest_price: Decimal,
    current_price: Decimal,
    indicators: Indicators,
    settings: Settings,
) -> ExitSignal:
    """Check trend-follow exit: trailing stop from peak OR EMA death cross."""
    # Trailing stop: price dropped trailing_stop_pct from highest observed
    trail_floor = highest_price * settings.tf_trailing_stop_multiplier
    if current_price <= trail_floor:
        return ExitSignal(True, "TRAILING_STOP")

    # Death cross: EMA9 crosses below EMA21
    if indicators.ema_short < indicators.ema_long:
        return ExitSignal(True, "DEATH_CROSS")

    return ExitSignal(False, "")
