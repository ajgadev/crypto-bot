"""Exponential Moving Average computation."""

from __future__ import annotations

from decimal import Decimal


def compute_ema(closes: list[Decimal], period: int) -> Decimal:
    """Compute EMA for the given period.

    Args:
        closes: List of closing prices (oldest first). Need at least `period` values.
        period: EMA period.

    Returns:
        Current EMA value.
    """
    if len(closes) < period:
        raise ValueError(f"Need at least {period} closes, got {len(closes)}")

    multiplier = Decimal("2") / (Decimal(str(period)) + Decimal("1"))

    # SMA as seed
    ema = sum(closes[:period]) / Decimal(str(period))

    # Apply EMA formula
    for close in closes[period:]:
        ema = (close - ema) * multiplier + ema

    return ema
