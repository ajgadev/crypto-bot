"""Balance vs state reconciliation."""

from __future__ import annotations

import logging
from decimal import Decimal

from src.binance.client import BinanceClient
from src.config.settings import Settings
from src.execution.state import StateStore
from src.notifications.telegram import TelegramNotifier

logger = logging.getLogger("crypto_bot")


async def reconcile_state(
    client: BinanceClient,
    state: StateStore,
    settings: Settings,
    notifier: TelegramNotifier | None = None,
) -> None:
    """Check that open trades match actual Binance balances.

    Forward: If a tracked symbol's balance is 0 but trade is 'open' in state,
    mark it as closed with reason 'EXTERNAL_CLOSE'.

    Reverse: If Binance has a non-zero balance for a configured symbol but no
    open trade exists in the DB, create a tracking record so the bot can manage it.
    """
    open_trades = state.get_open_trades()

    # ── Forward reconciliation: DB trade exists but Binance balance is 0 ──
    for trade in open_trades:
        base_asset = trade.symbol.replace(settings.quote_asset, "")
        balance = await client.get_asset_balance(base_asset)

        if balance <= Decimal("0"):
            logger.warning(
                "Trade %d (%s) has 0 balance on Binance, marking EXTERNAL_CLOSE",
                trade.id,
                trade.symbol,
                extra={"symbol": trade.symbol},
            )
            ticker = await client.get_ticker_price(trade.symbol)
            pnl = (ticker.price - trade.entry_price) * trade.entry_qty
            state.close_trade(
                trade_id=trade.id,
                exit_price=ticker.price,
                exit_reason="EXTERNAL_CLOSE",
                realized_pnl=pnl,
            )
            if notifier:
                await notifier.notify_external_close(trade.symbol, trade.id, pnl)

    # ── Reverse reconciliation: Binance balance exists but no DB trade ──
    tracked_symbols = {t.symbol for t in state.get_open_trades()}

    for symbol in settings.symbols_list:
        if symbol in tracked_symbols:
            continue

        base_asset = symbol.replace(settings.quote_asset, "")
        balance = await client.get_asset_balance(base_asset)

        if balance <= Decimal("0"):
            continue

        # Orphaned position detected — create a tracking record
        ticker = await client.get_ticker_price(symbol)
        notional = balance * ticker.price

        # Skip dust balances (< $1)
        if notional < Decimal("1"):
            continue

        logger.warning(
            "ORPHANED POSITION: %s has %s %s (~%s USDC) on Binance with no DB trade. "
            "Creating tracking record at current price %s.",
            symbol, balance, base_asset, notional.quantize(Decimal("0.01")),
            ticker.price,
            extra={"symbol": symbol},
        )

        state.insert_trade(
            symbol=symbol,
            side="BUY",
            entry_price=ticker.price,
            entry_qty=balance,
            idempotency_key=f"reconciled:{symbol}:{ticker.price}",
            strategy="mean_reversion",
        )
        if notifier:
            await notifier.notify_orphan(
                symbol, balance, base_asset, notional, ticker.price
            )
