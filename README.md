# Crypto Trading Bot

Production-ready, stateless crypto spot trading bot for the Binance Spot API.

- **Spot only** — no margin, no futures, no leverage, LONG only
- **Stateless per-run** — recomputes everything fresh from Binance data + SQLite state
- **3 modes** — `dry_run` (default), `live`, `backtest`
- **Beginner-safe** — testnet by default, reserve protection, idempotent execution
- **Telegram alerts** — instant notifications on trades, errors, portfolio reports with open position details
- **AI daily reports** — Claude Haiku generates market analysis sent via Telegram

## Strategies

### Mean Reversion
- **Indicators**: RSI(14), EMA(9/21), 24h percent change (1h candles)
- **Entry**: Bullish EMA bias + configurable 24h drop + RSI below threshold + trend filter (EMA 300)
- **Exit**: Take-profit / Stop-loss / RSI overbought
- **Safety net**: OCO order placed after each buy as backup
- **Regime-adaptive**: Auto-detects bull/bear market via BTC vs EMA200, switches to tighter TP/SL params in bear markets

### Trend Follow
- **Indicators**: RSI(14), EMA(20/50 configurable), volume SMA, EMA crossover detection
- **Entry**: Bullish EMA crossover + volume surge + RSI in momentum zone
- **Exit**: Trailing stop from highest observed price or EMA death cross

### Momentum
- **Indicators**: Same as Trend Follow (EMA crossover + volume + RSI)
- **Entry**: Same as Trend Follow — EMA crossover + volume surge + RSI in range
- **Exit**: Fixed take-profit (2.5%) / stop-loss (2.2%) — quick captures of crossover momentum
- **Safety net**: OCO order placed after each buy as backup
- Backtested at +15.4% return, 54% win rate on 2025-2026 data

All three strategies run independently with separate slot limits

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
python3 -m venv .venv
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
| `MEAN_REVERSION_RSI_EXIT` | `70` | RSI exit threshold |
| `MEAN_REVERSION_TREND_FILTER` | `true` | Enable EMA trend filter |
| `MEAN_REVERSION_TREND_EMA` | `300` | Trend filter EMA period |
| **Regime-Adaptive MR** | | |
| `MEAN_REVERSION_REGIME_ADAPTIVE` | `false` | Auto-switch MR params for bull/bear |
| `MEAN_REVERSION_REGIME_EMA` | `200` | EMA period for regime detection |
| `MEAN_REVERSION_REGIME_REFERENCE` | `BTCUSDC` | Reference symbol for regime |
| `MEAN_REVERSION_BEAR_RSI_MAX` | `50` | Bear market RSI threshold |
| `MEAN_REVERSION_BEAR_PCT_DROP` | `-0.01` | Bear market drop threshold |
| `MEAN_REVERSION_BEAR_TP_PCT` | `0.03` | Bear market take-profit |
| `MEAN_REVERSION_BEAR_SL_PCT` | `0.03` | Bear market stop-loss |
| **Risk Management** | | |
| `MAX_OPEN_TRADES` | `2` | Max concurrent MR positions |
| `RESERVE_PCT` | `0.10` | Reserve percentage of equity |
| `RISK_PCT` | `0.02` | Risk per trade as % of equity |
| `TAKE_PROFIT_PCT` | `0.04` | Take-profit percentage |
| `STOP_LOSS_PCT` | `0.05` | Stop-loss percentage |
| **Trend Follow** | | |
| `TREND_FOLLOW_ENABLED` | `true` | Enable trend-follow strategy |
| `TREND_FOLLOW_MAX_TRADES` | `2` | Max concurrent TF positions |
| `TREND_FOLLOW_TRAILING_STOP_PCT` | `0.15` | Trailing stop percentage from peak |
| `TREND_FOLLOW_RSI_MIN` | `50` | RSI min for entry |
| `TREND_FOLLOW_RSI_MAX` | `70` | RSI max for entry |
| `TREND_FOLLOW_VOLUME_MULTIPLIER` | `1.2` | Volume surge threshold |
| `TREND_FOLLOW_VOLUME_PERIOD` | `20` | Volume SMA period |
| `TREND_FOLLOW_CROSSOVER_WINDOW` | `3` | EMA crossover lookback |
| `TREND_FOLLOW_EMA_SHORT` | `20` | Short EMA period |
| `TREND_FOLLOW_EMA_LONG` | `50` | Long EMA period |
| **Momentum** | | |
| `MOMENTUM_ENABLED` | `false` | Enable momentum strategy |
| `MOMENTUM_SYMBOLS` | `` | Comma-separated symbols for MOM (empty = use global SYMBOLS) |
| `MOMENTUM_MAX_TRADES` | `2` | Max concurrent MOM positions |
| `MOMENTUM_TAKE_PROFIT_PCT` | `0.025` | Take-profit percentage |
| `MOMENTUM_STOP_LOSS_PCT` | `0.022` | Stop-loss percentage |
| `MOMENTUM_RSI_MIN` | `50` | RSI min for entry |
| `MOMENTUM_RSI_MAX` | `70` | RSI max for entry |
| `MOMENTUM_VOLUME_MULTIPLIER` | `1.2` | Volume surge threshold |
| `MOMENTUM_VOLUME_PERIOD` | `20` | Volume SMA period |
| `MOMENTUM_CROSSOVER_WINDOW` | `3` | EMA crossover lookback |
| `MOMENTUM_EMA_SHORT` | `20` | Short EMA period |
| `MOMENTUM_EMA_LONG` | `50` | Long EMA period |
| **Defensive Mode** | | |
| `DEFENSIVE_MODE_ENABLED` | `false` | Force-exit all positions in bear market |
| `DEFENSIVE_MODE_EMA` | `200` | EMA period for bear detection |
| `DEFENSIVE_MODE_REFERENCE` | `BTCUSDC` | Reference symbol |
| **Notifications** | | |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token from BotFather |
| `TELEGRAM_CHAT_ID` | — | Your Telegram chat ID |
| `TELEGRAM_REPORT_INTERVAL_HOURS` | `8` | Hours between portfolio reports |
| **AI Daily Report** | | |
| `ANTHROPIC_API_KEY` | — | Anthropic API key for Claude |
| `AI_DAILY_REPORT_ENABLED` | `false` | Enable AI-powered daily reports |
| `AI_DAILY_REPORT_HOUR` | `20` | UTC hour to send daily report |
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

## Deployment

### VPS (Hetzner)

Deploy to a VPS using the deploy script:

```bash
./scripts/deploy.sh <server-ip>
./scripts/deploy.sh <server-ip> --dry-run
```

This handles: system packages, code sync (rsync), Python venv, .env + DB sync, cron setup, and UFW firewall.

### CI/CD (GitHub Actions)

Auto-deploys on push to `main`. Requires these GitHub secrets:
- `VPS_HOST` — server IP
- `VPS_USER` — SSH user (e.g., `root`)
- `VPS_SSH_KEY` — private SSH key (no passphrase)

### Cron (manual)

Run every 15 minutes:

```bash
*/15 * * * * cd /path/to/crypto-bot && .venv/bin/python -m src.main >> logs/bot.log 2>> logs/cron_err.log
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
- Periodic portfolio reports with open position details (entry price, TP/SL targets, trailing stop, unrealized PnL)
- AI-powered daily market analysis (optional, requires Anthropic API key)

## Key Design Decisions

- **Decimal everywhere**: All price/quantity math uses Python `Decimal` to avoid floating-point drift
- **Idempotency**: Each action is keyed by `{strategy}:{symbol}:{action}:{candle_open_timestamp}` — duplicate cron fires won't double-trade
- **State reconciliation**: Every run checks Binance balances against local state, handling manual sells or OCO fills between runs
- **Fail-closed**: If the API is unreachable or state can't be locked, the bot exits without placing orders
- **Fire-and-forget notifications**: Telegram failures never crash the bot
