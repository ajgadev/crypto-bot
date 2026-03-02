"""Order placement and OCO logic."""

from __future__ import annotations

import logging
from decimal import Decimal

from src.binance.client import BinanceClient
from src.binance.filters import apply_lot_size, apply_price_filter
from src.binance.types import SymbolFilters
from src.config.settings import Settings
from src.execution.state import StateStore
from src.notifications.telegram import TelegramNotifier

logger = logging.getLogger("crypto_bot")


class OrderExecutor:
    """Handles order placement for live and dry_run modes."""

    def __init__(
        self,
        client: BinanceClient,
        state: StateStore,
        settings: Settings,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self._client = client
        self._state = state
        self._settings = settings
        self._notifier = notifier or TelegramNotifier("", "")

    async def execute_buy(
        self,
        symbol: str,
        quantity: Decimal,
        current_price: Decimal,
        filters: SymbolFilters,
        idempotency_key: str,
        strategy: str = "mean_reversion",
    ) -> bool:
        """Execute a market buy order + OCO safety net (mean_reversion only)."""
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
                        "strategy": strategy,
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
                strategy=strategy,
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
                        "strategy": strategy,
                    },
                },
            )
            await self._notifier.notify_buy(
                symbol, executed_qty, avg_price, strategy, executed_qty * avg_price
            )

            # Track actual filled quantity
            self._state.insert_trade(
                symbol=symbol,
                side="BUY",
                entry_price=avg_price,
                entry_qty=executed_qty,
                idempotency_key=idempotency_key,
                strategy=strategy,
            )
            self._state.record_idempotency(idempotency_key)

            # Place OCO safety net (mean_reversion only; trend_follow uses poll-driven exits)
            if strategy == "mean_reversion":
                await self._place_oco_safety(symbol, executed_qty, avg_price, filters)

            return True

        except Exception as e:
            logger.exception("Failed to execute BUY", extra={"symbol": symbol})
            await self._notifier.notify_error(f"BUY {symbol}", str(e))
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
        strategy: str = "mean_reversion",
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
            # Check actual balance — position may have been sold by OCO or externally
            base_asset = symbol.replace(self._settings.quote_asset, "")
            actual_balance = await self._client.get_asset_balance(base_asset)
            filters = await self._client.get_exchange_info(symbol)
            sell_qty = apply_lot_size(min(quantity, actual_balance), filters)

            if sell_qty <= 0:
                # Position already sold (OCO or external) — close in DB
                logger.warning(
                    "No %s balance to sell (likely OCO filled). Closing trade in DB as %s.",
                    symbol,
                    exit_reason,
                    extra={"symbol": symbol, "decision": f"OCO_CLOSED_{exit_reason}"},
                )
                self._state.close_trade(trade_id, current_price, exit_reason, pnl)
                self._state.record_idempotency(idempotency_key)
                await self._notifier.notify_sell(
                    symbol, quantity, current_price, strategy, pnl, exit_reason
                )
                return True

            order = await self._client.place_market_order(symbol, "SELL", sell_qty)

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
            await self._notifier.notify_sell(
                symbol, executed_qty, avg_price, strategy, actual_pnl, exit_reason
            )

            self._state.close_trade(trade_id, avg_price, exit_reason, actual_pnl)
            self._state.record_idempotency(idempotency_key)
            return True

        except Exception as e:
            logger.exception("Failed to execute SELL", extra={"symbol": symbol})
            await self._notifier.notify_error(f"SELL {symbol}", str(e))
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
