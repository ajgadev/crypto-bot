"""Balance vs state reconciliation."""

from __future__ import annotations

import logging
from decimal import Decimal

from src.binance.client import BinanceClient
from src.config.settings import Settings
from src.execution.state import StateStore

logger = logging.getLogger("crypto_bot")


async def reconcile_state(
    client: BinanceClient, state: StateStore, settings: Settings
) -> None:
    """Check that open trades match actual Binance balances.

    If a tracked symbol's balance is 0 but trade is 'open' in state,
    mark it as closed with reason 'EXTERNAL_CLOSE'.
    """
    open_trades = state.get_open_trades()
    if not open_trades:
        return

    for trade in open_trades:
        # Extract base asset from symbol (e.g., BTC from BTCUSDC)
        base_asset = trade.symbol.replace(settings.quote_asset, "")
        balance = await client.get_asset_balance(base_asset)

        if balance <= Decimal("0"):
            logger.warning(
                "Trade %d (%s) has 0 balance on Binance, marking EXTERNAL_CLOSE",
                trade.id,
                trade.symbol,
                extra={"symbol": trade.symbol},
            )
            # Get current price for PnL calculation
            ticker = await client.get_ticker_price(trade.symbol)
            pnl = (ticker.price - trade.entry_price) * trade.entry_qty
            state.close_trade(
                trade_id=trade.id,
                exit_price=ticker.price,
                exit_reason="EXTERNAL_CLOSE",
                realized_pnl=pnl,
            )
