"""AI-powered daily trading report using Claude API."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from src.execution.state import StateStore, Trade
from src.notifications.telegram import TelegramNotifier

logger = logging.getLogger("crypto_bot")


def _format_decimal(value: Decimal, decimals: int = 2) -> str:
    """Format a Decimal for display."""
    return f"{value:+.{decimals}f}"


def _compute_unrealized_pnl(
    entry_price: Decimal, entry_qty: Decimal, current_price: Decimal
) -> Decimal:
    """Compute unrealized PnL for a long position."""
    return (current_price - entry_price) * entry_qty


def gather_daily_data(
    state: StateStore,
    current_prices: dict[str, Decimal],
    regime: str | None = None,
    rejection_reasons: list[str] | None = None,
) -> dict:
    """Gather all data needed for the daily AI report.

    Args:
        state: The SQLite state store.
        current_prices: Mapping of symbol -> current market price.
        regime: Current market regime string (e.g. "bull", "bear").
        rejection_reasons: List of entry rejection reason strings from the current run.

    Returns:
        Dictionary with all report data.
    """
    # Open trades with unrealized PnL
    open_trades = state.get_open_trades()
    open_trade_data = []
    total_unrealized = Decimal("0")
    for trade in open_trades:
        price = current_prices.get(trade.symbol)
        unrealized = Decimal("0")
        if price is not None:
            unrealized = _compute_unrealized_pnl(
                trade.entry_price, trade.entry_qty, price
            )
        total_unrealized += unrealized
        open_trade_data.append({
            "symbol": trade.symbol,
            "strategy": trade.strategy,
            "entry_price": str(trade.entry_price),
            "current_price": str(price) if price else "N/A",
            "unrealized_pnl": _format_decimal(unrealized, 4),
        })

    # Closed trades in last 24h
    since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    closed_24h = state.get_closed_trades_since(since_24h)
    closed_trade_data = []
    pnl_24h = Decimal("0")
    for trade in closed_24h:
        pnl = trade.realized_pnl or Decimal("0")
        pnl_24h += pnl
        closed_trade_data.append({
            "symbol": trade.symbol,
            "strategy": trade.strategy,
            "pnl": _format_decimal(pnl, 4),
            "exit_reason": trade.exit_reason or "unknown",
        })

    # All-time stats by strategy
    all_closed = state.get_all_closed_trades()
    total_pnl = Decimal("0")
    strategy_stats: dict[str, dict] = {}
    for trade in all_closed:
        pnl = trade.realized_pnl or Decimal("0")
        total_pnl += pnl
        strat = trade.strategy
        if strat not in strategy_stats:
            strategy_stats[strat] = {"trades": 0, "wins": 0, "pnl": Decimal("0")}
        strategy_stats[strat]["trades"] += 1
        strategy_stats[strat]["pnl"] += pnl
        if pnl > 0:
            strategy_stats[strat]["wins"] += 1

    all_time_data = {
        "total_pnl": _format_decimal(total_pnl),
        "total_trades": len(all_closed),
        "by_strategy": {},
    }
    for strat, stats in strategy_stats.items():
        win_rate = (stats["wins"] / stats["trades"] * 100) if stats["trades"] > 0 else 0
        all_time_data["by_strategy"][strat] = {
            "trades": stats["trades"],
            "wins": stats["wins"],
            "win_rate": f"{win_rate:.0f}%",
            "pnl": _format_decimal(stats["pnl"]),
        }

    # Deduplicate and summarize rejection reasons
    rejection_summary: list[str] = []
    if rejection_reasons:
        # Count occurrences of each reason
        reason_counts: dict[str, int] = {}
        for reason in rejection_reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        rejection_summary = [
            f"{reason} (x{count})" if count > 1 else reason
            for reason, count in reason_counts.items()
        ]

    return {
        "open_trades": open_trade_data,
        "total_unrealized_pnl": _format_decimal(total_unrealized, 4),
        "closed_24h": closed_trade_data,
        "pnl_24h": _format_decimal(pnl_24h, 4),
        "all_time": all_time_data,
        "regime": regime or "unknown",
        "rejection_reasons": rejection_summary,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def build_prompt(data: dict) -> str:
    """Build the prompt for Claude to generate the daily report."""
    lines = [
        "You are a crypto trading bot's daily report writer. Generate a concise daily "
        "analysis (~200 words max) suitable for a Telegram message. Use plain text, no "
        "markdown or HTML tags. Be conversational but informative.",
        "",
        "Here is today's trading data:",
        "",
    ]

    # Open positions
    if data["open_trades"]:
        lines.append(f"OPEN POSITIONS ({len(data['open_trades'])}):")
        for t in data["open_trades"]:
            lines.append(
                f"  - {t['symbol']} ({t['strategy']}): entry {t['entry_price']}, "
                f"now {t['current_price']}, unrealized PnL: {t['unrealized_pnl']} USDC"
            )
        lines.append(f"  Total unrealized: {data['total_unrealized_pnl']} USDC")
    else:
        lines.append("OPEN POSITIONS: None")
    lines.append("")

    # Closed trades
    if data["closed_24h"]:
        lines.append(f"CLOSED TRADES (last 24h): {len(data['closed_24h'])}")
        for t in data["closed_24h"]:
            lines.append(
                f"  - {t['symbol']} ({t['strategy']}): PnL {t['pnl']} USDC, "
                f"exit: {t['exit_reason']}"
            )
        lines.append(f"  Total 24h PnL: {data['pnl_24h']} USDC")
    else:
        lines.append("CLOSED TRADES (last 24h): None")
    lines.append("")

    # All-time stats
    all_time = data["all_time"]
    lines.append(
        f"ALL-TIME: {all_time['total_trades']} trades, "
        f"total PnL: {all_time['total_pnl']} USDC"
    )
    for strat, stats in all_time.get("by_strategy", {}).items():
        lines.append(
            f"  - {strat}: {stats['trades']} trades, "
            f"win rate {stats['win_rate']}, PnL {stats['pnl']} USDC"
        )
    lines.append("")

    # Regime
    lines.append(f"MARKET REGIME: {data['regime']}")
    lines.append("")

    # Rejection reasons
    if data["rejection_reasons"]:
        lines.append("ENTRY REJECTIONS (why trades were NOT opened):")
        for reason in data["rejection_reasons"]:
            lines.append(f"  - {reason}")
    else:
        lines.append("ENTRY REJECTIONS: None (all conditions checked passed or no signals)")
    lines.append("")

    lines.append(
        "Please summarize: what happened today, why trades were or weren't made, "
        "note any concerns or opportunities. Keep it conversational and under 200 words."
    )

    return "\n".join(lines)


async def generate_ai_report(
    api_key: str,
    data: dict,
) -> str | None:
    """Call Claude API to generate the daily report text.

    Returns the report text, or None if generation fails.
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        logger.warning(
            "anthropic package not installed, skipping AI daily report. "
            "Install with: pip install anthropic"
        )
        return None

    if not api_key:
        logger.info("Anthropic API key not set, skipping AI daily report")
        return None

    prompt = build_prompt(data)

    try:
        client = Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        # Extract text from the response
        text = message.content[0].text if message.content else None
        return text
    except Exception:
        logger.exception("Failed to generate AI daily report")
        return None


async def send_daily_ai_report(
    state: StateStore,
    notifier: TelegramNotifier,
    api_key: str,
    current_prices: dict[str, Decimal],
    regime: str | None = None,
    rejection_reasons: list[str] | None = None,
) -> bool:
    """Gather data, generate AI report, and send via Telegram.

    Returns True if the report was sent successfully.
    """
    try:
        data = gather_daily_data(
            state=state,
            current_prices=current_prices,
            regime=regime,
            rejection_reasons=rejection_reasons,
        )

        report_text = await generate_ai_report(api_key=api_key, data=data)
        if report_text is None:
            return False

        header = f"🤖 AI Daily Report — {data['timestamp']}\n\n"
        await notifier.send(header + report_text)
        logger.info("AI daily report sent successfully")
        return True

    except Exception:
        logger.exception("Failed to send AI daily report")
        return False


def should_send_ai_report(state: StateStore, report_hour: int) -> bool:
    """Check if it's time to send the daily AI report.

    Uses the KV store to track when the last report was sent.
    Sends once per day at or after the configured hour (UTC).
    """
    now = datetime.now(timezone.utc)

    # Only send at or after the configured hour
    if now.hour < report_hour:
        return False

    last_sent = state.get_kv("last_ai_report_sent")
    if last_sent is None:
        return True

    try:
        last_dt = datetime.fromisoformat(last_sent)
        # Send if last report was sent on a different day (UTC)
        return last_dt.date() < now.date()
    except ValueError:
        return True


def mark_ai_report_sent(state: StateStore) -> None:
    """Record that the AI report was sent."""
    state.set_kv("last_ai_report_sent", datetime.now(timezone.utc).isoformat())
