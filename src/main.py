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
from src.logging.json_logger import setup_logging
from src.strategy.risk import compute_position_size
from src.strategy.signals import Indicators, check_entry_signal, check_exit_signal

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

            # Get open trades
            open_trades = state.get_open_trades()
            open_count = len(open_trades)
            slots_remaining = settings.max_open_trades - open_count

            # Compute positions value for equity
            positions_value = Decimal("0")
            for trade in open_trades:
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
                        "open_trades": open_count,
                        "slots": slots_remaining,
                    }
                },
            )

            # Process each symbol
            for symbol in settings.symbols_list:
                logger.info("Processing %s", symbol, extra={"symbol": symbol})

                try:
                    # Fetch data
                    klines = await client.get_klines(symbol, "1h", 50)
                    if len(klines) < 26:
                        logger.warning("Not enough klines for %s", symbol)
                        continue

                    # Use last closed candle (exclude current incomplete)
                    closed_klines = klines[:-1]
                    closes = [k.close for k in closed_klines]

                    # Compute indicators
                    rsi = compute_rsi(closes, 14)
                    ema9 = compute_ema(closes, 9)
                    ema21 = compute_ema(closes, 21)
                    pct_change = compute_pct_change_24h(closes) if len(closes) >= 25 else Decimal("0")

                    indicators = Indicators(
                        rsi=rsi,
                        ema9=ema9,
                        ema21=ema21,
                        pct_change_24h=pct_change,
                        last_close=closes[-1],
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
                            },
                            "bias": "BULLISH" if ema9 > ema21 else "BEARISH",
                        },
                    )

                    # Get current ticker price for exit checks
                    ticker = await client.get_ticker_price(symbol)
                    current_price = ticker.price

                    # ── Check exits ──
                    open_trade = state.get_open_trade_for_symbol(symbol)
                    if open_trade:
                        candle_open_ts = closed_klines[-1].open_time
                        exit_idemp_key = f"{symbol}:SELL:{candle_open_ts}"

                        if not state.check_idempotency(exit_idemp_key):
                            exit_signal = check_exit_signal(
                                open_trade.entry_price, current_price, rsi, settings
                            )
                            if exit_signal.should_exit:
                                logger.info(
                                    "Exit signal: %s",
                                    exit_signal.reason,
                                    extra={
                                        "symbol": symbol,
                                        "decision": f"EXIT_{exit_signal.reason}",
                                    },
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
                                # Refresh state
                                open_trades = state.get_open_trades()
                                open_count = len(open_trades)
                                slots_remaining = settings.max_open_trades - open_count
                        continue  # Don't enter same symbol we just exited or still hold

                    # ── Check entries ──
                    candle_open_ts = closed_klines[-1].open_time
                    entry_idemp_key = f"{symbol}:BUY:{candle_open_ts}"

                    if state.check_idempotency(entry_idemp_key):
                        logger.info("Idempotency: already acted on %s", entry_idemp_key)
                        continue

                    has_open = state.get_open_trade_for_symbol(symbol) is not None
                    entry_signal = check_entry_signal(
                        indicators, has_open, slots_remaining, tradable_usdt
                    )

                    logger.info(
                        "Entry signal: %s - %s",
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
                            slots_remaining=slots_remaining,
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
                            )
                            if success:
                                # Update tracking
                                open_trades = state.get_open_trades()
                                open_count = len(open_trades)
                                slots_remaining = settings.max_open_trades - open_count
                                free_usdt -= pos_size.notional
                                tradable_usdt = max(Decimal("0"), free_usdt - reserve_usdt)
                        else:
                            logger.info(
                                "Position sizing skip: %s",
                                pos_size.skip_reason,
                                extra={"symbol": symbol},
                            )

                except Exception:
                    logger.exception("Error processing %s", symbol, extra={"symbol": symbol})

    finally:
        state.close()


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
        # Extract symbol from filename (e.g., btcusdt_1h.csv -> BTCUSDT)
        basename = os.path.basename(csv_file).split("_")[0].upper()
        klines_by_symbol[basename] = load_csv(csv_file)  # type: ignore[assignment]
        logger.info("Loaded %d klines for %s", len(klines_by_symbol[basename]), basename)

    result = run_backtest(klines_by_symbol, settings=settings)  # type: ignore[arg-type]
    generate_report(result)


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
                "max_open_trades": settings.max_open_trades,
                "tp_pct": str(settings.take_profit_pct),
                "sl_pct": str(settings.stop_loss_pct),
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
