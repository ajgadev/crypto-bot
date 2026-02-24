"""Binance symbol filter helpers: LOT_SIZE, MIN_NOTIONAL, PRICE_FILTER."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from src.binance.types import SymbolFilters


def apply_lot_size(qty: Decimal, filters: SymbolFilters) -> Decimal:
    """Round quantity DOWN to the nearest lot step size."""
    if filters.lot_step_size == 0:
        return qty
    remainder = (qty - filters.lot_min_qty) % filters.lot_step_size
    rounded = qty - remainder
    # Ensure precision matches step size decimals
    step_str = str(filters.lot_step_size)
    if "." in step_str:
        decimals = len(step_str.rstrip("0").split(".")[1])
    else:
        decimals = 0
    return rounded.quantize(Decimal(10) ** -decimals, rounding=ROUND_DOWN)


def apply_price_filter(price: Decimal, filters: SymbolFilters) -> Decimal:
    """Round price to the nearest tick size."""
    if filters.price_tick_size == 0:
        return price
    tick_str = str(filters.price_tick_size)
    if "." in tick_str:
        decimals = len(tick_str.rstrip("0").split(".")[1])
    else:
        decimals = 0
    return price.quantize(Decimal(10) ** -decimals, rounding=ROUND_HALF_UP)


def check_min_notional(qty: Decimal, price: Decimal, filters: SymbolFilters) -> bool:
    """Return True if qty * price meets MIN_NOTIONAL."""
    return qty * price >= filters.min_notional
