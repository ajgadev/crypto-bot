"""Entry point: parse mode, acquire lock, run."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import sys
from decimal import Decimal

from src.binance.client import BinanceClient
from src.config.settings import RunMode, Settings
from src.execution.executor import OrderExecutor
from src.execution.reconciler import reconcile_state
from src.execution.state import StateStore
from src.indicators.ema import compute_ema
from src.indicators.percent_change import compute_pct_change_24h
from src.indicators.rsi import compute_rsi
from src.indicators.volume import compute_volume_sma
from src.logging.json_logger import setup_logging
from src.strategy.risk import compute_position_size
from src.strategy.signals import (
    Indicators,
    check_entry_signal,
    check_exit_signal,
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
    state = StateStore()
    state.connect()

    try:
        # Cleanup old idempotency keys
        cleaned = state.cleanup_old_idempotency_keys()
        if cleaned:
            logger.info("Cleaned %d old idempotency keys", cleaned)

        async with BinanceClient(settings) as client:
            executor = OrderExecutor(client, state, settings)

            # State reconciliation
            await reconcile_state(client, state, settings)

            # Get quote asset balance
            quote = settings.quote_asset
            free_usdt = await client.get_quote_balance(quote)
            logger.info("Free %s: %s", quote, free_usdt)

            # Get open trades per strategy
            all_open_trades = state.get_open_trades()
            mr_trades = [t for t in all_open_trades if t.strategy == "mean_reversion"]
            tf_trades = [t for t in all_open_trades if t.strategy == "trend_follow"]
            mr_slots = settings.max_open_trades - len(mr_trades)
            tf_slots = settings.trend_follow_max_trades - len(tf_trades)

            # Compute positions value for equity
            positions_value = Decimal("0")
            for trade in all_open_trades:
                ticker = await client.get_ticker_price(trade.symbol)
                positions_value += trade.entry_qty * ticker.price

            equity_usdt = free_usdt + positions_value
            reserve_usdt = max(Decimal("20"), equity_usdt * settings.reserve_pct)
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
                    }
                },
            )

            # Process each symbol
            for symbol in settings.symbols_list:
                logger.info("Processing %s", symbol, extra={"symbol": symbol})

                try:
                    # Fetch extra klines for prev-candle EMAs (need 52 to get prev EMA on 50)
                    klines = await client.get_klines(symbol, "1h", 52)
                    if len(klines) < 27:
                        logger.warning("Not enough klines for %s", symbol)
                        continue

                    # Use last closed candle (exclude current incomplete)
                    closed_klines = klines[:-1]
                    closes = [k.close for k in closed_klines]
                    volumes = [k.volume for k in closed_klines]

                    # Compute indicators on current candle
                    rsi = compute_rsi(closes, 14)
                    ema9 = compute_ema(closes, 9)
                    ema21 = compute_ema(closes, 21)
                    pct_change = compute_pct_change_24h(closes) if len(closes) >= 25 else Decimal("0")

                    # Prev-candle EMAs (for crossover detection)
                    prev_closes = closes[:-1]
                    prev_ema9 = compute_ema(prev_closes, 9) if len(prev_closes) >= 9 else None
                    prev_ema21 = compute_ema(prev_closes, 21) if len(prev_closes) >= 21 else None

                    # Volume indicators
                    vol_period = settings.trend_follow_volume_period
                    current_volume = volumes[-1] if volumes else None
                    avg_volume = (
                        compute_volume_sma(volumes, vol_period)
                        if len(volumes) >= vol_period
                        else None
                    )

                    indicators = Indicators(
                        rsi=rsi,
                        ema9=ema9,
                        ema21=ema21,
                        pct_change_24h=pct_change,
                        last_close=closes[-1],
                        prev_ema9=prev_ema9,
                        prev_ema21=prev_ema21,
                        current_volume=current_volume,
                        avg_volume=avg_volume,
                    )

                    logger.info(
                        "Indicators",
                        extra={
                            "symbol": symbol,
                            "indicators": {
                                "rsi": f"{rsi:.2f}",
                                "ema9": f"{ema9:.2f}",
                                "ema21": f"{ema21:.2f}",
                                "pct_24h": f"{pct_change:.4f}",
                                "last_close": str(closes[-1]),
                                "prev_ema9": f"{prev_ema9:.2f}" if prev_ema9 else "N/A",
                                "prev_ema21": f"{prev_ema21:.2f}" if prev_ema21 else "N/A",
                                "volume": str(current_volume) if current_volume else "N/A",
                                "avg_volume": f"{avg_volume:.2f}" if avg_volume else "N/A",
                            },
                            "bias": "BULLISH" if ema9 > ema21 else "BEARISH",
                        },
                    )

                    # Get current ticker price for exit checks
                    ticker = await client.get_ticker_price(symbol)
                    current_price = ticker.price
                    candle_open_ts = closed_klines[-1].open_time

                    # ── Mean-reversion exits & entries ──
                    if settings.mean_reversion_enabled:
                        mr_slots, tradable_usdt, free_usdt = await _process_mean_reversion(
                            symbol=symbol,
                            indicators=indicators,
                            current_price=current_price,
                            candle_open_ts=candle_open_ts,
                            state=state,
                            executor=executor,
                            client=client,
                            settings=settings,
                            mr_slots=mr_slots,
                            tradable_usdt=tradable_usdt,
                            free_usdt=free_usdt,
                            equity_usdt=equity_usdt,
                            reserve_usdt=reserve_usdt,
                            logger=logger,
                        )

                    # ── Trend-follow exits & entries ──
                    if settings.trend_follow_enabled:
                        tf_slots, tradable_usdt, free_usdt = await _process_trend_follow(
                            symbol=symbol,
                            indicators=indicators,
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
                        )

                except Exception:
                    logger.exception("Error processing %s", symbol, extra={"symbol": symbol})

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
