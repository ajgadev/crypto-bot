"""Pydantic models for Binance API responses."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class Kline(BaseModel):
    """Single candlestick data."""

    open_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time: int


class TickerPrice(BaseModel):
    """Current price for a symbol."""

    symbol: str
    price: Decimal


class SymbolFilters(BaseModel):
    """Parsed Binance symbol trading filters."""

    symbol: str
    min_notional: Decimal
    lot_step_size: Decimal
    lot_min_qty: Decimal
    lot_max_qty: Decimal
    price_tick_size: Decimal


class Fill(BaseModel):
    """Individual fill from an order response."""

    price: Decimal
    qty: Decimal
    commission: Decimal
    commission_asset: str = ""


class OrderResponse(BaseModel):
    """Binance order response."""

    symbol: str
    order_id: int = 0
    client_order_id: str = ""
    status: str = ""
    executed_qty: Decimal = Decimal("0")
    cummulative_quote_qty: Decimal = Decimal("0")
    fills: list[Fill] = []

    @property
    def avg_fill_price(self) -> Decimal:
        if not self.fills:
            if self.executed_qty > 0:
                return self.cummulative_quote_qty / self.executed_qty
            return Decimal("0")
        total_cost = sum(f.price * f.qty for f in self.fills)
        total_qty = sum(f.qty for f in self.fills)
        if total_qty == 0:
            return Decimal("0")
        return total_cost / total_qty
