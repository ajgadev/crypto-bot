"""Telegram bot notifications for trade alerts and error reporting."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import httpx

logger = logging.getLogger("crypto_bot")


@dataclass
class OpenPositionInfo:
    """Summary of an open position for the Telegram report."""

    symbol: str
    strategy: str
    entry_price: Decimal
    current_price: Decimal
    qty: Decimal
    unrealized_pnl: Decimal
    unrealized_pnl_pct: Decimal
    # MR specific
    tp_price: Optional[Decimal] = None
    sl_price: Optional[Decimal] = None
    # TF specific
    highest_price: Optional[Decimal] = None
    trailing_stop_price: Optional[Decimal] = None


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
        mr_pnl_total: Decimal | None = None,
        mr_trades_total: int = 0,
        mr_wins_total: int = 0,
        tf_pnl_total: Decimal | None = None,
        tf_trades_total: int = 0,
        tf_wins_total: int = 0,
        open_positions: list[OpenPositionInfo] | None = None,
    ) -> None:
        lines = [
            f"📊 <b>Portfolio Report</b>\n",
            f"Equity: <code>{equity:.2f}</code> USDC",
            f"Free: <code>{free:.2f}</code> USDC",
            f"Positions: <code>{positions_value:.2f}</code> USDC",
            f"Open trades: <code>{open_trades}</code>",
            f"MR slots: <code>{mr_slots}</code> | TF slots: <code>{tf_slots}</code>",
        ]

        # Open positions detail
        if open_positions:
            lines.append(f"\n📌 <b>Open Positions</b>")
            for pos in open_positions:
                pnl_emoji = "🟢" if pos.unrealized_pnl >= 0 else "🔴"
                strat_label = "MR" if pos.strategy == "mean_reversion" else "TF"
                pos_lines = [
                    f"\n<b>{pos.symbol}</b> [{strat_label}]",
                    f"  Entry: <code>{pos.entry_price}</code>",
                    f"  Now: <code>{pos.current_price}</code>",
                    f"  PnL: {pnl_emoji} <code>{pos.unrealized_pnl:+.2f}</code> USDC (<code>{pos.unrealized_pnl_pct:+.1f}%</code>)",
                ]
                if pos.strategy == "mean_reversion":
                    if pos.tp_price is not None:
                        tp_dist = (pos.tp_price / pos.current_price - 1) * 100
                        pos_lines.append(f"  TP: <code>{pos.tp_price}</code> ({tp_dist:+.1f}%)")
                    if pos.sl_price is not None:
                        sl_dist = (pos.sl_price / pos.current_price - 1) * 100
                        pos_lines.append(f"  SL: <code>{pos.sl_price}</code> ({sl_dist:+.1f}%)")
                elif pos.strategy == "trend_follow":
                    if pos.highest_price is not None:
                        pos_lines.append(f"  Peak: <code>{pos.highest_price}</code>")
                    if pos.trailing_stop_price is not None:
                        ts_dist = (pos.trailing_stop_price / pos.current_price - 1) * 100
                        pos_lines.append(f"  Trail stop: <code>{pos.trailing_stop_price}</code> ({ts_dist:+.1f}%)")
                lines.extend(pos_lines)

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
                f"PnL: {emoji} <code>{pnl_total:+.2f}</code> USDC | "
                f"Trades: <code>{trades_total}</code>"
            )

            if mr_trades_total > 0:
                mr_emoji = "🟢" if mr_pnl_total and mr_pnl_total >= 0 else "🔴"
                mr_wr = (mr_wins_total / mr_trades_total * 100) if mr_trades_total > 0 else 0
                lines.append(
                    f"  MR: {mr_emoji} <code>{mr_pnl_total:+.2f}</code> | "
                    f"{mr_trades_total} trades | WR <code>{mr_wr:.0f}%</code>"
                )

            if tf_trades_total > 0:
                tf_emoji = "🟢" if tf_pnl_total and tf_pnl_total >= 0 else "🔴"
                tf_wr = (tf_wins_total / tf_trades_total * 100) if tf_trades_total > 0 else 0
                lines.append(
                    f"  TF: {tf_emoji} <code>{tf_pnl_total:+.2f}</code> | "
                    f"{tf_trades_total} trades | WR <code>{tf_wr:.0f}%</code>"
                )

        await self.send("\n".join(lines))
