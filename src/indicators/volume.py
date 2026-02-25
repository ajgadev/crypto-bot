"""Volume-based indicators."""

from __future__ import annotations

from decimal import Decimal


def compute_volume_sma(volumes: list[Decimal], period: int = 20) -> Decimal:
    """Compute Simple Moving Average of volume over the given period.

    Args:
        volumes: List of volume values (oldest first). Need at least `period` values.
        period: SMA period.

    Returns:
        Average volume over the period.
    """
    if len(volumes) < period:
        raise ValueError(f"Need at least {period} volumes, got {len(volumes)}")

    return sum(volumes[-period:]) / Decimal(str(period))


def is_volume_confirmed(
    current_vol: Decimal, avg_vol: Decimal, multiplier: Decimal = Decimal("1.5")
) -> bool:
    """Check whether current volume exceeds the average by the given multiplier.

    Args:
        current_vol: Volume of the current (or last closed) candle.
        avg_vol: Average volume (e.g. SMA-20).
        multiplier: Required multiple (default 1.5x).

    Returns:
        True if current_vol >= avg_vol * multiplier.
    """
    if avg_vol <= 0:
        return False
    return current_vol >= avg_vol * multiplier
