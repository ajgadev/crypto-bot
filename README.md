# Crypto Trading Bot

Production-ready, stateless crypto spot trading bot for the Binance Spot API.

- **Spot only** — no margin, no futures, no leverage, LONG only
- **Stateless per-run** — recomputes everything fresh from Binance data + SQLite state
- **3 modes** — `dry_run` (default), `live`, `backtest`
- **Beginner-safe** — testnet by default, reserve protection, idempotent execution
- **Telegram alerts** — instant notifications on trades, errors, and periodic portfolio reports

## Strategies

### Mean Reversion
- **Indicators**: RSI(14), EMA(9/21), 24h percent change (1h candles)
- **Entry**: Bullish EMA bias + configurable 24h drop + RSI below threshold
- **Exit**: Take-profit / Stop-loss / RSI overbought
- **Safety net**: OCO order placed after each buy as backup

### Trend Follow
- **Indicators**: RSI(14), EMA(12/26 configurable), volume SMA, EMA crossover detection
- **Entry**: Bullish EMA crossover + volume surge + RSI in momentum zone
- **Exit**: Trailing stop from highest observed price
- Both strategies run independently with separate slot limits

## Quick Setup

```bash
git clone <your-repo> && cd crypto-bot
./setup.sh          # every 15 min (default)
./setup.sh 10       # or custom cron interval
```

The setup script will:
1. Install Python 3.11+ if not found (apt, dnf, yum, or brew)
2. Create virtual environment and install dependencies
3. Create `.env` from `.env.example`
4. Set up the cron job

Then edit `.env` with your credentials and you're running.

## Manual Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Binance API keys
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `BINANCE_API_KEY` | — | Your Binance API key |
| `BINANCE_API_SECRET` | — | Your Binance API secret |
| `BINANCE_TESTNET` | `true` | Use testnet (recommended for testing) |
| `RUN_MODE` | `dry_run` | `live`, `dry_run`, or `backtest` |
| `QUOTE_ASSET` | `USDC` | Quote currency |
| `SYMBOLS` | `BTCUSDC,ETHUSDC,BNBUSDC,SOLUSDC` | Comma-separated trading pairs |
| **Mean Reversion** | | |
| `MEAN_REVERSION_ENABLED` | `true` | Enable mean-reversion strategy |
| `MEAN_REVERSION_RSI_MAX` | `50` | RSI threshold for entry |
| `MEAN_REVERSION_PCT_DROP` | `-0.01` | 24h drop threshold |
| `MAX_OPEN_TRADES` | `2` | Max concurrent MR positions |
| `RESERVE_PCT` | `0.20` | Reserve percentage of equity |
| `RISK_PCT` | `0.02` | Risk per trade as % of equity |
| `TAKE_PROFIT_PCT` | `0.04` | Take-profit percentage |
| `STOP_LOSS_PCT` | `0.03` | Stop-loss percentage |
| **Trend Follow** | | |
| `TREND_FOLLOW_ENABLED` | `true` | Enable trend-follow strategy |
| `TREND_FOLLOW_MAX_TRADES` | `2` | Max concurrent TF positions |
| `TREND_FOLLOW_TRAILING_STOP_PCT` | `0.03` | Trailing stop percentage |
| `TREND_FOLLOW_RSI_MIN` | `50` | RSI min for entry |
| `TREND_FOLLOW_RSI_MAX` | `70` | RSI max for entry |
| `TREND_FOLLOW_VOLUME_MULTIPLIER` | `1.5` | Volume surge threshold |
| `TREND_FOLLOW_VOLUME_PERIOD` | `20` | Volume SMA period |
| `TREND_FOLLOW_CROSSOVER_WINDOW` | `3` | EMA crossover lookback |
| `TREND_FOLLOW_EMA_SHORT` | `12` | Short EMA period |
| `TREND_FOLLOW_EMA_LONG` | `26` | Long EMA period |
| **Notifications** | | |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token from BotFather |
| `TELEGRAM_CHAT_ID` | — | Your Telegram chat ID |
| `TELEGRAM_REPORT_INTERVAL_HOURS` | `8` | Hours between portfolio reports |
| **Logging** | | |
| `LOG_LEVEL` | `INFO` | Logging level |

## Running

### Dry Run (default — no real orders, real market data)
```bash
python -m src.main
```

### Live Trading
```bash
RUN_MODE=live BINANCE_TESTNET=false python -m src.main
```

### Backtesting

1. Download historical data:
```bash
python -m src.backtest.data_loader \
  --symbol BTCUSDC \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --output data/btcusdc_1h.csv
```

2. Run backtest:
```bash
RUN_MODE=backtest python -m src.main
```

## Tests

```bash
pytest tests/ -v
```

## Deployment (Cron)

Run every 15 minutes (or use `setup.sh`):

```bash
*/15 * * * * cd /path/to/crypto-bot && .venv/bin/python -m src.main >> logs/cron.log 2>&1
```

The bot uses a PID lock file (`/tmp/trading_bot.lock`) to prevent concurrent runs.

## Telegram Notifications

Create a bot via [@BotFather](https://t.me/BotFather), send it a message, then get your chat ID from:
```
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`. You'll receive:
- Buy/sell trade alerts with price, quantity, PnL, and strategy
- Error alerts when orders fail
- Orphaned position and external close warnings
- Periodic portfolio reports (every 8h by default)

## Key Design Decisions

- **Decimal everywhere**: All price/quantity math uses Python `Decimal` to avoid floating-point drift
- **Idempotency**: Each action is keyed by `{strategy}:{symbol}:{action}:{candle_open_timestamp}` — duplicate cron fires won't double-trade
- **State reconciliation**: Every run checks Binance balances against local state, handling manual sells or OCO fills between runs
- **Fail-closed**: If the API is unreachable or state can't be locked, the bot exits without placing orders
- **Fire-and-forget notifications**: Telegram failures never crash the bot
