"""Entry point: parse mode, acquire lock, run."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.binance.client import BinanceClient
from src.config.settings import RunMode, Settings
from src.execution.executor import OrderExecutor
from src.execution.reconciler import reconcile_state
from src.execution.state import StateStore
from src.notifications.telegram import OpenPositionInfo, TelegramNotifier
from src.indicators.ema import compute_ema
from src.indicators.percent_change import compute_pct_change_24h
from src.indicators.rsi import compute_rsi
from src.indicators.volume import compute_volume_sma
from src.logging.json_logger import setup_logging
from src.strategy.risk import compute_position_size
from src.reports.daily_ai import (
    mark_ai_report_sent,
    send_daily_ai_report,
    should_send_ai_report,
)
from src.strategy.signals import (
    Indicators,
    check_defensive_mode,
    check_entry_signal,
    check_exit_signal,
    check_momentum_entry,
    check_momentum_exit,
    check_trend_follow_entry,
    check_trend_follow_exit,
)

LOCK_FILE = "/tmp/trading_bot.lock"


def acquire_lock() -> int | None:
    """Acquire PID lock file. Returns fd on success, None if already locked."""
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, str(os.getpid()).encode())
        os.ftruncate(fd, len(str(os.getpid())))
        return fd
    except (OSError, BlockingIOError):
        return None


def release_lock(fd: int) -> None:
    """Release the PID lock."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        os.unlink(LOCK_FILE)
    except OSError:
        pass


async def run_live_or_dry(settings: Settings, logger: logging.Logger) -> None:
    """Execute live or dry_run trading loop."""
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    state = StateStore()
    state.connect()

    try:
        # Cleanup old idempotency keys
        cleaned = state.cleanup_old_idempotency_keys()
        if cleaned:
            logger.info("Cleaned %d old idempotency keys", cleaned)

        async with BinanceClient(settings) as client:
            executor = OrderExecutor(client, state, settings, notifier=notifier)

            # State reconciliation
            await reconcile_state(client, state, settings, notifier=notifier)

            # Get quote asset balance
            quote = settings.quote_asset
            free_usdt = await client.get_quote_balance(quote)
            logger.info("Free %s: %s", quote, free_usdt)

            # Get open trades per strategy
            all_open_trades = state.get_open_trades()
            mr_trades = [t for t in all_open_trades if t.strategy == "mean_reversion"]
            tf_trades = [t for t in all_open_trades if t.strategy == "trend_follow"]
            mom_trades = [t for t in all_open_trades if t.strategy == "momentum"]
            mr_slots = settings.max_open_trades - len(mr_trades)
            tf_slots = settings.trend_follow_max_trades - len(tf_trades)
            mom_slots = settings.momentum_max_trades - len(mom_trades)

            # Compute positions value for equity + build open position info
            positions_value = Decimal("0")
            open_position_infos: list[OpenPositionInfo] = []
            for trade in all_open_trades:
                ticker = await client.get_ticker_price(trade.symbol)
                cur_price = ticker.price
                positions_value += trade.entry_qty * cur_price

                notional = trade.entry_qty * trade.entry_price
                unrealized = trade.entry_qty * cur_price - notional
                unrealized_pct = (cur_price / trade.entry_price - 1) * 100

                pos_info = OpenPositionInfo(
                    symbol=trade.symbol,
                    strategy=trade.strategy,
                    entry_price=trade.entry_price,
                    current_price=cur_price,
                    qty=trade.entry_qty,
                    unrealized_pnl=unrealized,
                    unrealized_pnl_pct=unrealized_pct,
                )

                if trade.strategy == "mean_reversion":
                    pos_info.tp_price = trade.entry_price * settings.tp_multiplier
                    pos_info.sl_price = trade.entry_price * settings.sl_multiplier
                elif trade.strategy == "momentum":
                    pos_info.tp_price = trade.entry_price * settings.momentum_tp_multiplier
                    pos_info.sl_price = trade.entry_price * settings.momentum_sl_multiplier
                elif trade.strategy == "trend_follow":
                    highest = trade.highest_price or trade.entry_price
                    if cur_price > highest:
                        highest = cur_price
                    pos_info.highest_price = highest
                    pos_info.trailing_stop_price = highest * settings.tf_trailing_stop_multiplier

                open_position_infos.append(pos_info)

            equity_usdt = free_usdt + positions_value
            reserve_usdt = max(Decimal("5"), equity_usdt * settings.reserve_pct)
            tradable_usdt = max(Decimal("0"), free_usdt - reserve_usdt)

            logger.info(
                "Portfolio",
                extra={
                    "budgets": {
                        "equity": str(equity_usdt),
                        "free": str(free_usdt),
                        "positions_value": str(positions_value),
                        "reserve": str(reserve_usdt),
                        "tradable": str(tradable_usdt),
                        "mr_trades": len(mr_trades),
                        "mr_slots": mr_slots,
                        "tf_trades": len(tf_trades),
                        "tf_slots": tf_slots,
                        "mom_trades": len(mom_trades),
                        "mom_slots": mom_slots,
                    }
                },
            )

            # Periodic Telegram portfolio report
            if settings.telegram_report_interval_hours > 0:
                now_iso = datetime.now(timezone.utc).isoformat()
                last_report = state.get_kv("last_report_sent")
                send_report = False
                if last_report is None:
                    send_report = True
                else:
                    try:
                        last_dt = datetime.fromisoformat(last_report)
                        elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
                        send_report = elapsed_hours >= settings.telegram_report_interval_hours
                    except ValueError:
                        send_report = True

                if send_report:
                    # Compute PnL stats for report
                    since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
                    closed_24h = state.get_closed_trades_since(since_24h)
                    pnl_24h = sum(t.realized_pnl for t in closed_24h if t.realized_pnl is not None)
                    wins_24h = sum(1 for t in closed_24h if t.realized_pnl and t.realized_pnl > 0)

                    all_closed = state.get_all_closed_trades()
                    pnl_total = sum(t.realized_pnl for t in all_closed if t.realized_pnl is not None)

                    mr_closed = [t for t in all_closed if t.strategy == "mean_reversion"]
                    mr_pnl = sum(t.realized_pnl for t in mr_closed if t.realized_pnl is not None)
                    mr_wins = sum(1 for t in mr_closed if t.realized_pnl and t.realized_pnl > 0)

                    tf_closed = [t for t in all_closed if t.strategy == "trend_follow"]
                    tf_pnl = sum(t.realized_pnl for t in tf_closed if t.realized_pnl is not None)
                    tf_wins = sum(1 for t in tf_closed if t.realized_pnl and t.realized_pnl > 0)

                    mom_closed = [t for t in all_closed if t.strategy == "momentum"]
                    mom_pnl = sum(t.realized_pnl for t in mom_closed if t.realized_pnl is not None)
                    mom_wins = sum(1 for t in mom_closed if t.realized_pnl and t.realized_pnl > 0)

                    await notifier.notify_report(
                        equity=equity_usdt,
                        free=free_usdt,
                        positions_value=positions_value,
                        open_trades=len(all_open_trades),
                        mr_slots=mr_slots,
                        tf_slots=tf_slots,
                        mom_slots=mom_slots,
                        pnl_24h=pnl_24h,
                        trades_24h=len(closed_24h),
                        wins_24h=wins_24h,
                        pnl_total=pnl_total,
                        trades_total=len(all_closed),
                        mr_pnl_total=mr_pnl,
                        mr_trades_total=len(mr_closed),
                        mr_wins_total=mr_wins,
                        tf_pnl_total=tf_pnl,
                        tf_trades_total=len(tf_closed),
                        tf_wins_total=tf_wins,
                        mom_pnl_total=mom_pnl,
                        mom_trades_total=len(mom_closed),
                        mom_wins_total=mom_wins,
                        open_positions=open_position_infos,
                    )
                    state.set_kv("last_report_sent", now_iso)

            # Collectors for AI daily report
            rejection_reasons: list[str] = []
            symbol_prices: dict[str, Decimal] = {}
            regime_label: str | None = None

            # ── Regime-adaptive MR check ──
            mr_settings = settings
            if settings.mean_reversion_regime_adaptive:
                try:
                    regime_ref = settings.mean_reversion_regime_reference
                    regime_ema = settings.mean_reversion_regime_ema
                    regime_klines = await client.get_klines(
                        regime_ref, "1h", regime_ema + 10
                    )
                    regime_closes = [k.close for k in regime_klines[:-1]]
                    if len(regime_closes) >= regime_ema:
                        regime_ema_val = compute_ema(regime_closes[-(regime_ema + 10):], regime_ema)
                        is_bear_regime = regime_closes[-1] < regime_ema_val
                        if is_bear_regime:
                            mr_settings = settings.with_bear_mr_params()
                        regime_label = "bear" if is_bear_regime else "bull"
                        logger.info(
                            "MR regime: %s",
                            "BEAR (using bear params)" if is_bear_regime else "BULL (normal params)",
                            extra={
                                "mr_regime": "bear" if is_bear_regime else "bull",
                                "reference": regime_ref,
                                "close": str(regime_closes[-1]),
                                "ema": str(regime_ema_val),
                            },
                        )
                except Exception:
                    logger.exception("Failed regime check, using default MR params")

            # ── Defensive mode check ──
            is_bear = False
            if settings.defensive_mode_enabled:
                ref_symbol = settings.defensive_mode_reference
                try:
                    ref_klines = await client.get_klines(
                        ref_symbol, "1h", settings.defensive_mode_ema + 10
                    )
                    ref_closes = [k.close for k in ref_klines[:-1]]  # exclude current incomplete
                    is_bear = check_defensive_mode(ref_closes, settings)
                    logger.info(
                        "Defensive mode: %s",
                        "BEAR (blocking entries)" if is_bear else "BULL (normal)",
                        extra={"defensive_mode": is_bear, "reference": ref_symbol},
                    )
                except Exception:
                    logger.exception("Failed to check defensive mode for %s", ref_symbol)

            if is_bear:
                # Force-exit all open positions
                for trade in all_open_trades:
                    ticker = await client.get_ticker_price(trade.symbol)
                    logger.info(
                        "DEFENSIVE_EXIT: closing %s %s position",
                        trade.strategy,
                        trade.symbol,
                        extra={"symbol": trade.symbol, "decision": "DEFENSIVE_EXIT"},
                    )
                    await executor.execute_sell(
                        trade_id=trade.id,
                        symbol=trade.symbol,
                        quantity=trade.entry_qty,
                        current_price=ticker.price,
                        entry_price=trade.entry_price,
                        exit_reason="DEFENSIVE_EXIT",
                        idempotency_key=f"defensive:{trade.symbol}:{trade.id}",
                        strategy=trade.strategy,
                    )
                logger.info("Defensive mode active — skipping all entries")
            else:
                # Normal processing — process each symbol
                for symbol in settings.symbols_list:
                    logger.info("Processing %s", symbol, extra={"symbol": symbol})

                    try:
                        # Fetch enough klines for all indicators (trend EMA may need up to 300+)
                        ema_fetch = settings.trend_follow_ema_long + settings.trend_follow_crossover_window + 25
                        mom_ema_fetch = settings.momentum_ema_long + settings.momentum_crossover_window + 25
                        trend_fetch = settings.mean_reversion_trend_ema + 10 if settings.mean_reversion_trend_filter else 0
                        klines = await client.get_klines(symbol, "1h", max(52, ema_fetch, mom_ema_fetch, trend_fetch))
                        if len(klines) < settings.trend_follow_ema_long + 2:
                            logger.warning("Not enough klines for %s", symbol)
                            continue

                        # Use last closed candle (exclude current incomplete)
                        closed_klines = klines[:-1]
                        closes = [k.close for k in closed_klines]
                        volumes = [k.volume for k in closed_klines]

                        # Compute shared indicators
                        rsi = compute_rsi(closes, 14)
                        pct_change = compute_pct_change_24h(closes) if len(closes) >= 25 else Decimal("0")

                        # MR uses fixed EMA 9/21 for bullish bias + trend EMA
                        mr_ema9 = compute_ema(closes, 9)
                        mr_ema21 = compute_ema(closes, 21)
                        mr_trend_period = settings.mean_reversion_trend_ema
                        mr_ema_trend = (
                            compute_ema(closes, mr_trend_period)
                            if len(closes) >= mr_trend_period
                            else None
                        )

                        # TF uses configurable EMA periods
                        tf_ema_short_period = settings.trend_follow_ema_short
                        tf_ema_long_period = settings.trend_follow_ema_long
                        tf_ema_short = compute_ema(closes, tf_ema_short_period)
                        tf_ema_long = compute_ema(closes, tf_ema_long_period)

                        # EMA history for TF crossover detection (last N candles)
                        crossover_window = settings.trend_follow_crossover_window
                        ema_short_history: list[Decimal] = []
                        ema_long_history: list[Decimal] = []
                        for offset in range(crossover_window, 0, -1):
                            hist_closes = closes[:-offset]
                            if len(hist_closes) >= tf_ema_long_period:
                                ema_short_history.append(compute_ema(hist_closes, tf_ema_short_period))
                                ema_long_history.append(compute_ema(hist_closes, tf_ema_long_period))

                        # Volume indicators
                        vol_period = settings.trend_follow_volume_period
                        current_volume = volumes[-1] if volumes else None
                        avg_volume = (
                            compute_volume_sma(volumes, vol_period)
                            if len(volumes) >= vol_period
                            else None
                        )

                        # MR indicators (EMA 9/21 + trend filter)
                        mr_indicators = Indicators(
                            rsi=rsi,
                            ema_short=mr_ema9,
                            ema_long=mr_ema21,
                            pct_change_24h=pct_change,
                            last_close=closes[-1],
                            ema_trend=mr_ema_trend,
                        )

                        # TF indicators (configurable EMAs + history + volume)
                        tf_indicators = Indicators(
                            rsi=rsi,
                            ema_short=tf_ema_short,
                            ema_long=tf_ema_long,
                            pct_change_24h=pct_change,
                            last_close=closes[-1],
                            ema_short_history=ema_short_history or None,
                            ema_long_history=ema_long_history or None,
                            current_volume=current_volume,
                            avg_volume=avg_volume,
                        )

                        logger.info(
                            "Indicators",
                            extra={
                                "symbol": symbol,
                                "indicators": {
                                    "rsi": f"{rsi:.2f}",
                                    "ema9": f"{mr_ema9:.2f}",
                                    "ema21": f"{mr_ema21:.2f}",
                                    f"tf_ema{tf_ema_short_period}": f"{tf_ema_short:.2f}",
                                    f"tf_ema{tf_ema_long_period}": f"{tf_ema_long:.2f}",
                                    "pct_24h": f"{pct_change:.4f}",
                                    "last_close": str(closes[-1]),
                                    "volume": str(current_volume) if current_volume else "N/A",
                                    "avg_volume": f"{avg_volume:.2f}" if avg_volume else "N/A",
                                },
                                "mr_bias": "BULLISH" if mr_ema9 > mr_ema21 else "BEARISH",
                                "tf_bias": "BULLISH" if tf_ema_short > tf_ema_long else "BEARISH",
                            },
                        )

                        # Get current ticker price for exit checks
                        ticker = await client.get_ticker_price(symbol)
                        current_price = ticker.price
                        symbol_prices[symbol] = current_price
                        candle_open_ts = closed_klines[-1].open_time

                        # ── Mean-reversion exits & entries ──
                        if settings.mean_reversion_enabled:
                            mr_slots, tradable_usdt, free_usdt = await _process_mean_reversion(
                                symbol=symbol,
                                indicators=mr_indicators,
                                current_price=current_price,
                                candle_open_ts=candle_open_ts,
                                state=state,
                                executor=executor,
                                client=client,
                                settings=mr_settings,
                                mr_slots=mr_slots,
                                tradable_usdt=tradable_usdt,
                                free_usdt=free_usdt,
                                equity_usdt=equity_usdt,
                                reserve_usdt=reserve_usdt,
                                logger=logger,
                                rejection_reasons=rejection_reasons,
                            )

                        # ── Trend-follow exits & entries ──
                        if settings.trend_follow_enabled:
                            tf_slots, tradable_usdt, free_usdt = await _process_trend_follow(
                                symbol=symbol,
                                indicators=tf_indicators,
                                current_price=current_price,
                                candle_open_ts=candle_open_ts,
                                state=state,
                                executor=executor,
                                client=client,
                                settings=settings,
                                tf_slots=tf_slots,
                                tradable_usdt=tradable_usdt,
                                free_usdt=free_usdt,
                                equity_usdt=equity_usdt,
                                reserve_usdt=reserve_usdt,
                                logger=logger,
                                rejection_reasons=rejection_reasons,
                            )

                        # ── Momentum exits & entries ──
                        if settings.momentum_enabled:
                            # Build momentum indicators (may differ from TF if EMA periods differ)
                            mom_ema_short_period = settings.momentum_ema_short
                            mom_ema_long_period = settings.momentum_ema_long
                            if (mom_ema_short_period == tf_ema_short_period
                                    and mom_ema_long_period == tf_ema_long_period):
                                mom_indicators = tf_indicators
                            else:
                                mom_ema_short = compute_ema(closes, mom_ema_short_period)
                                mom_ema_long = compute_ema(closes, mom_ema_long_period)
                                mom_crossover_window = settings.momentum_crossover_window
                                mom_ema_short_history: list[Decimal] = []
                                mom_ema_long_history: list[Decimal] = []
                                for offset in range(mom_crossover_window, 0, -1):
                                    hist_closes = closes[:-offset]
                                    if len(hist_closes) >= mom_ema_long_period:
                                        mom_ema_short_history.append(compute_ema(hist_closes, mom_ema_short_period))
                                        mom_ema_long_history.append(compute_ema(hist_closes, mom_ema_long_period))
                                mom_vol_period = settings.momentum_volume_period
                                mom_avg_volume = (
                                    compute_volume_sma(volumes, mom_vol_period)
                                    if len(volumes) >= mom_vol_period
                                    else None
                                )
                                mom_indicators = Indicators(
                                    rsi=rsi,
                                    ema_short=mom_ema_short,
                                    ema_long=mom_ema_long,
                                    pct_change_24h=pct_change,
                                    last_close=closes[-1],
                                    ema_short_history=mom_ema_short_history or None,
                                    ema_long_history=mom_ema_long_history or None,
                                    current_volume=current_volume,
                                    avg_volume=mom_avg_volume,
                                )

                            mom_slots, tradable_usdt, free_usdt = await _process_momentum(
                                symbol=symbol,
                                indicators=mom_indicators,
                                current_price=current_price,
                                candle_open_ts=candle_open_ts,
                                state=state,
                                executor=executor,
                                client=client,
                                settings=settings,
                                mom_slots=mom_slots,
                                tradable_usdt=tradable_usdt,
                                free_usdt=free_usdt,
                                equity_usdt=equity_usdt,
                                reserve_usdt=reserve_usdt,
                                logger=logger,
                                rejection_reasons=rejection_reasons,
                            )

                    except Exception:
                        logger.exception("Error processing %s", symbol, extra={"symbol": symbol})

            # ── AI daily report ──
            if settings.ai_daily_report_enabled and should_send_ai_report(
                state, settings.ai_daily_report_hour
            ):
                logger.info("Generating AI daily report")
                sent = await send_daily_ai_report(
                    state=state,
                    notifier=notifier,
                    api_key=settings.anthropic_api_key,
                    current_prices=symbol_prices,
                    regime=regime_label,
                    rejection_reasons=rejection_reasons,
                )
                if sent:
                    mark_ai_report_sent(state)

    finally:
        state.close()


async def _process_mean_reversion(
    *,
    symbol: str,
    indicators: Indicators,
    current_price: Decimal,
    candle_open_ts: int,
    state: StateStore,
    executor: OrderExecutor,
    client: BinanceClient,
    settings: Settings,
    mr_slots: int,
    tradable_usdt: Decimal,
    free_usdt: Decimal,
    equity_usdt: Decimal,
    reserve_usdt: Decimal,
    logger: logging.Logger,
    rejection_reasons: list[str] | None = None,
) -> tuple[int, Decimal, Decimal]:
    """Process mean-reversion exit/entry for one symbol. Returns updated (mr_slots, tradable, free)."""
    open_trade = state.get_open_trade_for_symbol(symbol, strategy="mean_reversion")

    if open_trade:
        exit_idemp_key = f"mean_reversion:{symbol}:SELL:{candle_open_ts}"
        if not state.check_idempotency(exit_idemp_key):
            exit_signal = check_exit_signal(
                open_trade.entry_price, current_price, indicators.rsi, settings
            )
            if exit_signal.should_exit:
                logger.info(
                    "MR exit signal: %s",
                    exit_signal.reason,
                    extra={"symbol": symbol, "decision": f"MR_EXIT_{exit_signal.reason}"},
                )
                await executor.execute_sell(
                    trade_id=open_trade.id,
                    symbol=symbol,
                    quantity=open_trade.entry_qty,
                    current_price=current_price,
                    entry_price=open_trade.entry_price,
                    exit_reason=exit_signal.reason,
                    idempotency_key=exit_idemp_key,
                    strategy="mean_reversion",
                )
                mr_trades = state.get_open_trades(strategy="mean_reversion")
                mr_slots = settings.max_open_trades - len(mr_trades)
        return mr_slots, tradable_usdt, free_usdt  # Don't enter same symbol we still hold

    # ── Check entries ──
    entry_idemp_key = f"mean_reversion:{symbol}:BUY:{candle_open_ts}"
    if state.check_idempotency(entry_idemp_key):
        logger.info("Idempotency: already acted on %s", entry_idemp_key)
        return mr_slots, tradable_usdt, free_usdt

    has_open = state.get_open_trade_for_symbol(symbol, strategy="mean_reversion") is not None
    entry_signal = check_entry_signal(indicators, has_open, mr_slots, tradable_usdt, settings)

    logger.info(
        "MR entry signal: %s - %s",
        entry_signal.should_enter,
        entry_signal.reason,
        extra={"symbol": symbol, "decision": entry_signal.reason},
    )

    if not entry_signal.should_enter and rejection_reasons is not None:
        rejection_reasons.append(f"MR {symbol}: {entry_signal.reason}")

    if entry_signal.should_enter:
        filters = await client.get_exchange_info(symbol)
        pos_size = compute_position_size(
            current_price=current_price,
            free_usdt=free_usdt,
            equity_usdt=equity_usdt,
            slots_remaining=mr_slots,
            filters=filters,
            settings=settings,
        )

        if pos_size.can_trade:
            success = await executor.execute_buy(
                symbol=symbol,
                quantity=pos_size.quantity,
                current_price=current_price,
                filters=filters,
                idempotency_key=entry_idemp_key,
                strategy="mean_reversion",
            )
            if success:
                mr_trades = state.get_open_trades(strategy="mean_reversion")
                mr_slots = settings.max_open_trades - len(mr_trades)
                free_usdt -= pos_size.notional
                tradable_usdt = max(Decimal("0"), free_usdt - reserve_usdt)
        else:
            logger.info("MR position sizing skip: %s", pos_size.skip_reason, extra={"symbol": symbol})

    return mr_slots, tradable_usdt, free_usdt


async def _process_trend_follow(
    *,
    symbol: str,
    indicators: Indicators,
    current_price: Decimal,
    candle_open_ts: int,
    state: StateStore,
    executor: OrderExecutor,
    client: BinanceClient,
    settings: Settings,
    tf_slots: int,
    tradable_usdt: Decimal,
    free_usdt: Decimal,
    equity_usdt: Decimal,
    reserve_usdt: Decimal,
    logger: logging.Logger,
    rejection_reasons: list[str] | None = None,
) -> tuple[int, Decimal, Decimal]:
    """Process trend-follow exit/entry for one symbol. Returns updated (tf_slots, tradable, free)."""
    open_trade = state.get_open_trade_for_symbol(symbol, strategy="trend_follow")

    if open_trade:
        # Update highest observed price
        state.update_highest_price(open_trade.id, current_price)
        highest = max(open_trade.highest_price or open_trade.entry_price, current_price)

        exit_idemp_key = f"trend_follow:{symbol}:SELL:{candle_open_ts}"
        if not state.check_idempotency(exit_idemp_key):
            exit_signal = check_trend_follow_exit(
                entry_price=open_trade.entry_price,
                highest_price=highest,
                current_price=current_price,
                indicators=indicators,
                settings=settings,
            )
            if exit_signal.should_exit:
                logger.info(
                    "TF exit signal: %s",
                    exit_signal.reason,
                    extra={"symbol": symbol, "decision": f"TF_EXIT_{exit_signal.reason}"},
                )
                await executor.execute_sell(
                    trade_id=open_trade.id,
                    symbol=symbol,
                    quantity=open_trade.entry_qty,
                    current_price=current_price,
                    entry_price=open_trade.entry_price,
                    exit_reason=exit_signal.reason,
                    idempotency_key=exit_idemp_key,
                    strategy="trend_follow",
                )
                tf_trades = state.get_open_trades(strategy="trend_follow")
                tf_slots = settings.trend_follow_max_trades - len(tf_trades)
        return tf_slots, tradable_usdt, free_usdt

    # ── Check entries ──
    entry_idemp_key = f"trend_follow:{symbol}:BUY:{candle_open_ts}"
    if state.check_idempotency(entry_idemp_key):
        logger.info("Idempotency: already acted on %s", entry_idemp_key)
        return tf_slots, tradable_usdt, free_usdt

    has_open = state.get_open_trade_for_symbol(symbol, strategy="trend_follow") is not None
    entry_signal = check_trend_follow_entry(
        indicators, has_open, tf_slots, tradable_usdt, settings
    )

    logger.info(
        "TF entry signal: %s - %s",
        entry_signal.should_enter,
        entry_signal.reason,
        extra={"symbol": symbol, "decision": entry_signal.reason},
    )

    if not entry_signal.should_enter and rejection_reasons is not None:
        rejection_reasons.append(f"TF {symbol}: {entry_signal.reason}")

    if entry_signal.should_enter:
        filters = await client.get_exchange_info(symbol)
        pos_size = compute_position_size(
            current_price=current_price,
            free_usdt=free_usdt,
            equity_usdt=equity_usdt,
            slots_remaining=tf_slots,
            filters=filters,
            settings=settings,
        )

        if pos_size.can_trade:
            success = await executor.execute_buy(
                symbol=symbol,
                quantity=pos_size.quantity,
                current_price=current_price,
                filters=filters,
                idempotency_key=entry_idemp_key,
                strategy="trend_follow",
            )
            if success:
                tf_trades = state.get_open_trades(strategy="trend_follow")
                tf_slots = settings.trend_follow_max_trades - len(tf_trades)
                free_usdt -= pos_size.notional
                tradable_usdt = max(Decimal("0"), free_usdt - reserve_usdt)
        else:
            logger.info("TF position sizing skip: %s", pos_size.skip_reason, extra={"symbol": symbol})

    return tf_slots, tradable_usdt, free_usdt


async def _process_momentum(
    *,
    symbol: str,
    indicators: Indicators,
    current_price: Decimal,
    candle_open_ts: int,
    state: StateStore,
    executor: OrderExecutor,
    client: BinanceClient,
    settings: Settings,
    mom_slots: int,
    tradable_usdt: Decimal,
    free_usdt: Decimal,
    equity_usdt: Decimal,
    reserve_usdt: Decimal,
    logger: logging.Logger,
    rejection_reasons: list[str] | None = None,
) -> tuple[int, Decimal, Decimal]:
    """Process momentum exit/entry for one symbol. Returns updated (mom_slots, tradable, free)."""
    open_trade = state.get_open_trade_for_symbol(symbol, strategy="momentum")

    if open_trade:
        exit_idemp_key = f"momentum:{symbol}:SELL:{candle_open_ts}"
        if not state.check_idempotency(exit_idemp_key):
            exit_signal = check_momentum_exit(
                open_trade.entry_price, current_price, settings
            )
            if exit_signal.should_exit:
                logger.info(
                    "MOM exit signal: %s",
                    exit_signal.reason,
                    extra={"symbol": symbol, "decision": f"MOM_EXIT_{exit_signal.reason}"},
                )
                await executor.execute_sell(
                    trade_id=open_trade.id,
                    symbol=symbol,
                    quantity=open_trade.entry_qty,
                    current_price=current_price,
                    entry_price=open_trade.entry_price,
                    exit_reason=exit_signal.reason,
                    idempotency_key=exit_idemp_key,
                    strategy="momentum",
                )
                mom_trades = state.get_open_trades(strategy="momentum")
                mom_slots = settings.momentum_max_trades - len(mom_trades)
        return mom_slots, tradable_usdt, free_usdt

    # ── Check entries ──
    entry_idemp_key = f"momentum:{symbol}:BUY:{candle_open_ts}"
    if state.check_idempotency(entry_idemp_key):
        logger.info("Idempotency: already acted on %s", entry_idemp_key)
        return mom_slots, tradable_usdt, free_usdt

    has_open = state.get_open_trade_for_symbol(symbol, strategy="momentum") is not None
    entry_signal = check_momentum_entry(
        indicators, has_open, mom_slots, tradable_usdt, settings
    )

    logger.info(
        "MOM entry signal: %s - %s",
        entry_signal.should_enter,
        entry_signal.reason,
        extra={"symbol": symbol, "decision": entry_signal.reason},
    )

    if not entry_signal.should_enter and rejection_reasons is not None:
        rejection_reasons.append(f"MOM {symbol}: {entry_signal.reason}")

    if entry_signal.should_enter:
        filters = await client.get_exchange_info(symbol)
        # Use momentum SL for risk-based sizing
        mom_settings = settings.model_copy(update={
            "stop_loss_pct": settings.momentum_stop_loss_pct,
        })
        pos_size = compute_position_size(
            current_price=current_price,
            free_usdt=free_usdt,
            equity_usdt=equity_usdt,
            slots_remaining=mom_slots,
            filters=filters,
            settings=mom_settings,
        )

        if pos_size.can_trade:
            success = await executor.execute_buy(
                symbol=symbol,
                quantity=pos_size.quantity,
                current_price=current_price,
                filters=filters,
                idempotency_key=entry_idemp_key,
                strategy="momentum",
            )
            if success:
                mom_trades = state.get_open_trades(strategy="momentum")
                mom_slots = settings.momentum_max_trades - len(mom_trades)
                free_usdt -= pos_size.notional
                tradable_usdt = max(Decimal("0"), free_usdt - reserve_usdt)
        else:
            logger.info("MOM position sizing skip: %s", pos_size.skip_reason, extra={"symbol": symbol})

    return mom_slots, tradable_usdt, free_usdt


async def run_backtest_mode(settings: Settings, logger: logging.Logger) -> None:
    """Run backtesting engine."""
    import glob as glob_mod

    from src.backtest.data_loader import load_csv
    from src.backtest.engine import run_backtest
    from src.backtest.report import generate_report

    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    csv_files = glob_mod.glob(os.path.join(data_dir, "*.csv"))

    if not csv_files:
        logger.error("No CSV files found in data/ directory. Download data first.")
        logger.info(
            "Example: python -m src.backtest.data_loader "
            "--symbol BTCUSDC --start 2024-01-01 --end 2024-12-31 "
            "--output data/btcusdc_1h.csv"
        )
        return

    klines_by_symbol: dict[str, list[object]] = {}
    for csv_file in csv_files:
        fname = os.path.basename(csv_file)
        # Skip non-kline CSVs (e.g. backtest_trades.csv)
        if not fname.endswith("_1h.csv"):
            continue
        # Extract symbol from filename (e.g., btcusdc_1h.csv -> BTCUSDC)
        basename = fname.split("_")[0].upper()
        klines_by_symbol[basename] = load_csv(csv_file)  # type: ignore[assignment]
        logger.info("Loaded %d klines for %s", len(klines_by_symbol[basename]), basename)

    if not klines_by_symbol:
        logger.error("No kline CSV files found (expected *_1h.csv pattern)")
        return

    result = run_backtest(klines_by_symbol, settings=settings)  # type: ignore[arg-type]
    report_dir = os.path.join(data_dir, "reports")
    generate_report(result, output_dir=report_dir)


async def main() -> None:
    """Main entry point."""
    settings = Settings()
    logger = setup_logging(settings.log_level)

    # Testnet warning
    if not settings.binance_testnet:
        logger.warning("*** RUNNING ON MAINNET - REAL MONEY AT RISK ***")
    else:
        logger.info("Running on TESTNET")

    logger.info(
        "Bot starting",
        extra={
            "decision": f"mode={settings.run_mode}",
            "budgets": {
                "symbols": settings.symbols_list,
                "mean_reversion_enabled": settings.mean_reversion_enabled,
                "max_open_trades": settings.max_open_trades,
                "tp_pct": str(settings.take_profit_pct),
                "sl_pct": str(settings.stop_loss_pct),
                "trend_follow_enabled": settings.trend_follow_enabled,
                "tf_max_trades": settings.trend_follow_max_trades,
                "tf_trailing_stop": str(settings.trend_follow_trailing_stop_pct),
                "momentum_enabled": settings.momentum_enabled,
                "mom_max_trades": settings.momentum_max_trades,
                "mom_tp": str(settings.momentum_take_profit_pct),
                "mom_sl": str(settings.momentum_stop_loss_pct),
            },
        },
    )

    if settings.run_mode == RunMode.BACKTEST:
        await run_backtest_mode(settings, logger)
        return

    # Acquire lock
    lock_fd = acquire_lock()
    if lock_fd is None:
        logger.error("Another instance is already running. Exiting.")
        sys.exit(1)

    try:
        await run_live_or_dry(settings, logger)
    finally:
        release_lock(lock_fd)

    logger.info("Bot run complete")


if __name__ == "__main__":
    asyncio.run(main())
