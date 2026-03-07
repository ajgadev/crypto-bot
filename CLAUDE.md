# Crypto Trading Bot

## Project Overview
Automated crypto spot trading bot for Binance. Runs via cron every 15 minutes. Two strategies: Mean Reversion (MR) and Trend Follow (TF). Uses 1h candles for indicators.

## Tech Stack
- Python 3.12+, async (asyncio + httpx)
- pydantic-settings for config (loads from `.env`)
- SQLite for state (trades, idempotency keys, KV store)
- Telegram for notifications
- Tests: pytest + pytest-asyncio

## Project Structure
```
src/
  main.py              # Entry point, orchestrates both strategies
  config/settings.py   # All settings via env vars (pydantic-settings)
  binance/client.py    # Binance API client (httpx async)
  binance/filters.py   # Exchange filters (lot size, notional, etc.)
  binance/types.py     # Data types (Kline, Ticker, etc.)
  strategy/signals.py  # Entry/exit signal logic for MR and TF
  strategy/risk.py     # Position sizing
  indicators/          # RSI, EMA, volume SMA, percent change
  execution/
    executor.py        # Order execution (buy/sell)
    state.py           # SQLite state store (trades, idempotency)
    reconciler.py      # Reconcile local state with exchange
  notifications/telegram.py  # Telegram notifier
  backtest/            # Backtesting engine, data loader, report
  logging/json_logger.py
tests/                 # pytest tests
data/                  # Backtest CSV data and reports
```

## Key Commands
```bash
# Run bot (dry run)
python -m src.main

# Run tests
pytest

# Lint
ruff check src/ tests/

# Backtest
RUN_MODE=backtest python -m src.main
```

## Strategies
- **Mean Reversion (MR)**: Buys on RSI dip + 24h price drop + bullish EMA bias (9/21) + trend filter (EMA 300). Exits on TP/SL/RSI threshold.
- **Trend Follow (TF)**: Buys on EMA crossover (20/50) + volume confirmation + RSI range. Exits on trailing stop from peak or death cross.

## Architecture Notes
- Config: all via env vars, see `src/config/settings.py` for defaults
- Idempotency: keys based on `{strategy}:{symbol}:{side}:{candle_open_ts}` prevent duplicate orders within same candle
- Lock file (`/tmp/trading_bot.lock`) prevents concurrent runs
- State: SQLite DB tracks open/closed trades, highest price (for trailing stop), KV pairs
- Defensive mode: optional bear market protection using reference symbol EMA

## Run Modes
- `live` — real trades on Binance mainnet
- `dry_run` — simulated trades, real market data
- `backtest` — historical data from CSV files
