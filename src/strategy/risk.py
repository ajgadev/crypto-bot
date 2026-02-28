"""Position sizing and reserve management."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from src.binance.filters import apply_lot_size, check_min_notional
from src.binance.types import SymbolFilters
from src.config.settings import Settings

logger = logging.getLogger("crypto_bot")


@dataclass
class PositionSize:
    """Computed position sizing result."""

    quantity: Decimal
    notional: Decimal
    can_trade: bool
    skip_reason: str


def compute_position_size(
    current_price: Decimal,
    free_usdt: Decimal,
    equity_usdt: Decimal,
    slots_remaining: int,
    filters: SymbolFilters,
    settings: Settings,
) -> PositionSize:
    """Compute order quantity respecting all risk rules and Binance filters."""
    # Reserve
    reserve_usdt = max(Decimal("5"), equity_usdt * settings.reserve_pct)
    tradable_usdt = max(Decimal("0"), free_usdt - reserve_usdt)

    if tradable_usdt <= 0:
        logger.warning(
            "Account too small to trade: equity=%s, reserve=%s, free=%s. Consider adding funds.",
            equity_usdt, reserve_usdt, free_usdt,
        )
        return PositionSize(Decimal("0"), Decimal("0"), False, "No tradable budget after reserve")

    if slots_remaining <= 0:
        return PositionSize(Decimal("0"), Decimal("0"), False, "No trade slots")

    # Per-trade cap
    per_trade_cap = tradable_usdt / Decimal(str(slots_remaining))

    # Risk-based sizing
    risk_budget = equity_usdt * settings.risk_pct
    if settings.stop_loss_pct > 0:
        notional_by_risk = risk_budget / settings.stop_loss_pct
    else:
        notional_by_risk = per_trade_cap

    # Final notional
    order_notional = min(per_trade_cap, notional_by_risk)

    # Clamp up to MIN_NOTIONAL if risk sizing dips below but funds allow it
    if order_notional < filters.min_notional and tradable_usdt >= filters.min_notional:
        logger.info(
            "Risk-sized notional %s below MIN_NOTIONAL %s, clamping up (tradable=%s)",
            order_notional, filters.min_notional, tradable_usdt,
        )
        order_notional = filters.min_notional

    # Fee buffer (0.1%)
    order_notional *= Decimal("0.999")

    # Quantity
    raw_qty = order_notional / current_price
    qty = apply_lot_size(raw_qty, filters)

    if qty <= 0:
        return PositionSize(Decimal("0"), Decimal("0"), False, "Quantity rounded to 0")

    # MIN_NOTIONAL check
    actual_notional = qty * current_price
    if not check_min_notional(qty, current_price, filters):
        return PositionSize(
            qty,
            actual_notional,
            False,
            f"Below MIN_NOTIONAL ({filters.min_notional})",
        )

    logger.info(
        "Position sized",
        extra={
            "budgets": {
                "free_usdt": str(free_usdt),
                "equity_usdt": str(equity_usdt),
                "reserve_usdt": str(reserve_usdt),
                "tradable_usdt": str(tradable_usdt),
                "per_trade_cap": str(per_trade_cap),
                "order_notional": str(actual_notional),
                "quantity": str(qty),
            }
        },
    )

    return PositionSize(qty, actual_notional, True, "")
