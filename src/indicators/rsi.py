"""RSI(14) with Wilder smoothing."""

from __future__ import annotations

from decimal import Decimal


def compute_rsi(closes: list[Decimal], period: int = 14) -> Decimal:
    """Compute RSI using Wilder's smoothing method.

    Args:
        closes: List of closing prices (oldest first). Need at least period+1 values.
        period: RSI period (default 14).

    Returns:
        RSI value as Decimal (0-100).
    """
    if len(closes) < period + 1:
        raise ValueError(f"Need at least {period + 1} closes, got {len(closes)}")

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # Initial average gain/loss from first `period` deltas
    gains = [d if d > 0 else Decimal("0") for d in deltas[:period]]
    losses = [-d if d < 0 else Decimal("0") for d in deltas[:period]]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Wilder smoothing for remaining deltas
    for delta in deltas[period:]:
        gain = delta if delta > 0 else Decimal("0")
        loss = -delta if delta < 0 else Decimal("0")
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return Decimal("100")

    rs = avg_gain / avg_loss
    rsi = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
    return rsi
