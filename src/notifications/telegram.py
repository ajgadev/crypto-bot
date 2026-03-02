"""Telegram bot notifications for trade alerts and error reporting."""

from __future__ import annotations

import logging
from decimal import Decimal

import httpx

logger = logging.getLogger("crypto_bot")


class TelegramNotifier:
    """Fire-and-forget Telegram notifications via Bot API."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)
        if self._enabled:
            self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    async def send(self, message: str) -> None:
        """Send a message. No-op if not configured. Never raises."""
        if not self._enabled:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self._url,
                    json={
                        "chat_id": self._chat_id,
                        "text": message,
                        "parse_mode": "HTML",
                    },
                )
                if resp.status_code != 200:
                    logger.warning("Telegram send failed: %s", resp.text)
        except Exception:
            logger.debug("Telegram notification failed", exc_info=True)

    async def notify_buy(
        self,
        symbol: str,
        qty: Decimal,
        price: Decimal,
        strategy: str,
        notional: Decimal,
    ) -> None:
        msg = (
            f"🟢 <b>BUY {symbol}</b>\n"
            f"Qty: <code>{qty}</code>\n"
            f"Price: <code>{price}</code>\n"
            f"Notional: <code>{notional:.2f}</code> USDC\n"
            f"Strategy: <i>{strategy}</i>"
        )
        await self.send(msg)

    async def notify_sell(
        self,
        symbol: str,
        qty: Decimal,
        price: Decimal,
        strategy: str,
        pnl: Decimal,
        exit_reason: str,
    ) -> None:
        emoji = "🟢" if pnl >= 0 else "🔴"
        msg = (
            f"🔻 <b>SELL {symbol}</b>\n"
            f"Qty: <code>{qty}</code>\n"
            f"Price: <code>{price}</code>\n"
            f"PnL: {emoji} <code>{pnl:+.4f}</code> USDC\n"
            f"Reason: <i>{exit_reason}</i>\n"
            f"Strategy: <i>{strategy}</i>"
        )
        await self.send(msg)

    async def notify_error(self, context: str, error_msg: str) -> None:
        msg = (
            f"⚠️ <b>ERROR</b>\n"
            f"Context: <i>{context}</i>\n"
            f"<pre>{error_msg}</pre>"
        )
        await self.send(msg)

    async def notify_orphan(
        self,
        symbol: str,
        qty: Decimal,
        base_asset: str,
        notional: Decimal,
        price: Decimal,
    ) -> None:
        msg = (
            f"👻 <b>ORPHANED POSITION</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Balance: <code>{qty} {base_asset}</code>\n"
            f"Value: <code>~{notional:.2f}</code> USDC\n"
            f"Price: <code>{price}</code>\n"
            f"<i>Created tracking record</i>"
        )
        await self.send(msg)

    async def notify_external_close(
        self, symbol: str, trade_id: int, pnl: Decimal
    ) -> None:
        emoji = "🟢" if pnl >= 0 else "🔴"
        msg = (
            f"🔔 <b>EXTERNAL CLOSE</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Trade ID: <code>{trade_id}</code>\n"
            f"PnL: {emoji} <code>{pnl:+.4f}</code> USDC\n"
            f"<i>Position closed outside bot</i>"
        )
        await self.send(msg)

    async def notify_report(
        self,
        equity: Decimal,
        free: Decimal,
        positions_value: Decimal,
        open_trades: int,
        mr_slots: int,
        tf_slots: int,
        pnl_24h: Decimal | None = None,
        trades_24h: int = 0,
        wins_24h: int = 0,
        pnl_total: Decimal | None = None,
        trades_total: int = 0,
    ) -> None:
        lines = [
            f"📊 <b>Portfolio Report</b>\n",
            f"Equity: <code>{equity:.2f}</code> USDC",
            f"Free: <code>{free:.2f}</code> USDC",
            f"Positions: <code>{positions_value:.2f}</code> USDC",
            f"Open trades: <code>{open_trades}</code>",
            f"MR slots: <code>{mr_slots}</code> | TF slots: <code>{tf_slots}</code>",
        ]

        if pnl_24h is not None:
            emoji = "🟢" if pnl_24h >= 0 else "🔴"
            wr = (wins_24h / trades_24h * 100) if trades_24h > 0 else 0
            lines.append(
                f"\n📈 <b>Last 24h</b>\n"
                f"PnL: {emoji} <code>{pnl_24h:+.2f}</code> USDC\n"
                f"Trades: <code>{trades_24h}</code> | Win rate: <code>{wr:.0f}%</code>"
            )

        if pnl_total is not None:
            emoji = "🟢" if pnl_total >= 0 else "🔴"
            lines.append(
                f"\n📉 <b>All-time</b>\n"
                f"PnL: {emoji} <code>{pnl_total:+.2f}</code> USDC\n"
                f"Trades: <code>{trades_total}</code>"
            )

        await self.send("\n".join(lines))
