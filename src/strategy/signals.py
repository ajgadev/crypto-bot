"""Entry and exit signal generation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from src.config.settings import Settings

logger = logging.getLogger("crypto_bot")


@dataclass
class Indicators:
    """Computed indicator values for a symbol."""

    rsi: Decimal
    ema9: Decimal
    ema21: Decimal
    pct_change_24h: Decimal
    last_close: Decimal


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
    reason: str  # TP, SL, RSI_EXIT, or empty


def check_entry_signal(
    indicators: Indicators,
    has_open_trade: bool,
    slots_remaining: int,
    tradable_usdt: Decimal,
) -> EntrySignal:
    """Evaluate all entry conditions. ALL must be true for a LONG entry."""
    # Bullish bias: EMA(9) > EMA(21)
    is_bullish = indicators.ema9 > indicators.ema21
    if not is_bullish:
        return EntrySignal(False, "Bearish bias (EMA9 <= EMA21)", indicators)

    # 24h drop >= 3%
    if indicators.pct_change_24h > Decimal("-0.03"):
        return EntrySignal(
            False,
            f"24h change {indicators.pct_change_24h:.4f} > -0.03",
            indicators,
        )

    # RSI < 35
    if indicators.rsi >= Decimal("35"):
        return EntrySignal(False, f"RSI {indicators.rsi:.2f} >= 35", indicators)

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

    # RSI > 65
    if rsi > Decimal("65"):
        return ExitSignal(True, "RSI_EXIT")

    return ExitSignal(False, "")
