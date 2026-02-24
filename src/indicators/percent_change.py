"""24-hour percent change computation."""

from __future__ import annotations

from decimal import Decimal


def compute_pct_change_24h(closes: list[Decimal]) -> Decimal:
    """Compute 24h percent change from hourly candle closes.

    Uses close_now = closes[-1] and close_24h_ago = closes[-25].
    Requires at least 25 candle closes.
    """
    if len(closes) < 25:
        raise ValueError(f"Need at least 25 closes, got {len(closes)}")

    close_now = closes[-1]
    close_24h_ago = closes[-25]

    if close_24h_ago == 0:
        return Decimal("0")

    return (close_now / close_24h_ago) - Decimal("1")
