"""Order placement and OCO logic."""

from __future__ import annotations

import logging
from decimal import Decimal

from src.binance.client import BinanceClient
from src.binance.filters import apply_lot_size, apply_price_filter
from src.binance.types import SymbolFilters
from src.config.settings import Settings
from src.execution.state import StateStore

logger = logging.getLogger("crypto_bot")


class OrderExecutor:
    """Handles order placement for live and dry_run modes."""

    def __init__(
        self,
        client: BinanceClient,
        state: StateStore,
        settings: Settings,
    ) -> None:
        self._client = client
        self._state = state
        self._settings = settings

    async def execute_buy(
        self,
        symbol: str,
        quantity: Decimal,
        current_price: Decimal,
        filters: SymbolFilters,
        idempotency_key: str,
    ) -> bool:
        """Execute a market buy order + OCO safety net."""
        is_dry_run = self._settings.run_mode == "dry_run"

        if is_dry_run:
            logger.info(
                "DRY RUN: Would BUY",
                extra={
                    "symbol": symbol,
                    "order_params": {
                        "side": "BUY",
                        "quantity": str(quantity),
                        "price": str(current_price),
                    },
                    "decision": "DRY_RUN_BUY",
                },
            )
            # Simulate fill in state
            self._state.insert_trade(
                symbol=symbol,
                side="BUY",
                entry_price=current_price,
                entry_qty=quantity,
                idempotency_key=idempotency_key,
            )
            self._state.record_idempotency(idempotency_key)
            return True

        # Live execution
        try:
            order = await self._client.place_market_order(symbol, "BUY", quantity)

            executed_qty = order.executed_qty
            avg_price = order.avg_fill_price

            if executed_qty == 0:
                logger.error("BUY order returned 0 executed qty", extra={"symbol": symbol})
                return False

            if executed_qty < quantity:
                logger.warning(
                    "Partial fill: requested %s, got %s",
                    quantity,
                    executed_qty,
                    extra={"symbol": symbol},
                )

            logger.info(
                "BUY filled",
                extra={
                    "symbol": symbol,
                    "result": {
                        "executed_qty": str(executed_qty),
                        "avg_price": str(avg_price),
                        "status": order.status,
                    },
                },
            )

            # Track actual filled quantity
            self._state.insert_trade(
                symbol=symbol,
                side="BUY",
                entry_price=avg_price,
                entry_qty=executed_qty,
                idempotency_key=idempotency_key,
            )
            self._state.record_idempotency(idempotency_key)

            # Place OCO safety net
            await self._place_oco_safety(symbol, executed_qty, avg_price, filters)

            return True

        except Exception:
            logger.exception("Failed to execute BUY", extra={"symbol": symbol})
            return False

    async def execute_sell(
        self,
        trade_id: int,
        symbol: str,
        quantity: Decimal,
        current_price: Decimal,
        entry_price: Decimal,
        exit_reason: str,
        idempotency_key: str,
    ) -> bool:
        """Execute a market sell order."""
        is_dry_run = self._settings.run_mode == "dry_run"

        pnl = (current_price - entry_price) * quantity

        if is_dry_run:
            logger.info(
                "DRY RUN: Would SELL",
                extra={
                    "symbol": symbol,
                    "order_params": {
                        "side": "SELL",
                        "quantity": str(quantity),
                        "price": str(current_price),
                        "exit_reason": exit_reason,
                        "pnl": str(pnl),
                    },
                    "decision": "DRY_RUN_SELL",
                },
            )
            self._state.close_trade(trade_id, current_price, exit_reason, pnl)
            self._state.record_idempotency(idempotency_key)
            return True

        # Live execution
        try:
            order = await self._client.place_market_order(symbol, "SELL", quantity)

            executed_qty = order.executed_qty
            avg_price = order.avg_fill_price
            actual_pnl = (avg_price - entry_price) * executed_qty

            if executed_qty < quantity:
                logger.warning(
                    "Partial SELL fill: requested %s, got %s",
                    quantity,
                    executed_qty,
                    extra={"symbol": symbol},
                )

            logger.info(
                "SELL filled",
                extra={
                    "symbol": symbol,
                    "result": {
                        "executed_qty": str(executed_qty),
                        "avg_price": str(avg_price),
                        "exit_reason": exit_reason,
                        "pnl": str(actual_pnl),
                    },
                },
            )

            self._state.close_trade(trade_id, avg_price, exit_reason, actual_pnl)
            self._state.record_idempotency(idempotency_key)
            return True

        except Exception:
            logger.exception("Failed to execute SELL", extra={"symbol": symbol})
            return False

    async def _place_oco_safety(
        self,
        symbol: str,
        quantity: Decimal,
        entry_price: Decimal,
        filters: SymbolFilters,
    ) -> None:
        """Place OCO order as safety net after a buy fill."""
        try:
            tp_price = apply_price_filter(
                entry_price * self._settings.tp_multiplier, filters
            )
            sl_trigger = apply_price_filter(
                entry_price * self._settings.sl_multiplier, filters
            )
            sl_limit = apply_price_filter(
                entry_price * self._settings.sl_limit_multiplier, filters
            )
            qty = apply_lot_size(quantity, filters)

            await self._client.place_oco_order(
                symbol=symbol,
                side="SELL",
                quantity=qty,
                price=tp_price,
                stop_price=sl_trigger,
                stop_limit_price=sl_limit,
            )
            logger.info(
                "OCO placed",
                extra={
                    "symbol": symbol,
                    "order_params": {
                        "tp_price": str(tp_price),
                        "sl_trigger": str(sl_trigger),
                        "sl_limit": str(sl_limit),
                    },
                },
            )
        except Exception:
            logger.exception(
                "OCO placement failed (polling exit still active)",
                extra={"symbol": symbol, "error": "OCO_FAILED"},
            )
