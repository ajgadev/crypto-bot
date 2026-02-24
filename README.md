# Crypto Trading Bot

Production-ready, stateless crypto spot trading bot for the Binance Spot API.

- **Spot only** — no margin, no futures, no leverage, LONG only
- **Stateless per-run** — recomputes everything fresh from Binance data + SQLite state
- **3 modes** — `dry_run` (default), `live`, `backtest`
- **Beginner-safe** — testnet by default, reserve protection, idempotent execution

## Strategy

- **Indicators**: RSI(14), EMA(9), EMA(21), 24h percent change (1h candles)
- **Entry**: Bullish bias + 3% 24h drop + RSI < 35
- **Exit**: Take-profit (4%, configurable) / Stop-loss (3%, configurable) / RSI > 65
- **Safety net**: OCO order placed after each buy as backup

## Setup

```bash
# Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure
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
| `SYMBOLS` | `BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT` | Comma-separated trading pairs |
| `MAX_OPEN_TRADES` | `2` | Maximum concurrent open positions |
| `RESERVE_PCT` | `0.20` | Reserve percentage of equity |
| `RISK_PCT` | `0.02` | Risk per trade as % of equity |
| `TAKE_PROFIT_PCT` | `0.04` | Take-profit percentage (4%) |
| `STOP_LOSS_PCT` | `0.03` | Stop-loss percentage (3%) |
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
  --symbol BTCUSDT \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --output data/btcusdt_1h.csv
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

Run every 15 minutes:

```bash
*/15 * * * * cd /path/to/crypto-bot && .venv/bin/python -m src.main >> /dev/null 2>&1
```

The bot uses a PID lock file (`/tmp/trading_bot.lock`) to prevent concurrent runs.

## Key Design Decisions

- **Decimal everywhere**: All price/quantity math uses Python `Decimal` to avoid floating-point drift
- **Idempotency**: Each action is keyed by `{symbol}:{action}:{candle_open_timestamp}` — duplicate cron fires won't double-trade
- **State reconciliation**: Every run checks Binance balances against local state, handling manual sells or OCO fills between runs
- **Fail-closed**: If the API is unreachable or state can't be locked, the bot exits without placing orders
- **Configurable TP/SL**: Take-profit and stop-loss percentages are set via environment variables (`TAKE_PROFIT_PCT`, `STOP_LOSS_PCT`)
