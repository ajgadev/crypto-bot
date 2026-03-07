#!/usr/bin/env bash
# Healthcheck: alerts via Telegram if the bot hasn't logged activity recently.
# Designed to run via cron every hour:
#   0 * * * * /opt/bots/crypto-bot/scripts/healthcheck.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/bot.log"
MAX_AGE_MINUTES=45  # alert if no log entry in this many minutes

# Load Telegram credentials from .env
if [ -f "$SCRIPT_DIR/.env" ]; then
    TELEGRAM_BOT_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$SCRIPT_DIR/.env" | cut -d= -f2-)
    TELEGRAM_CHAT_ID=$(grep -E '^TELEGRAM_CHAT_ID=' "$SCRIPT_DIR/.env" | cut -d= -f2-)
fi

send_alert() {
    local message="$1"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="$TELEGRAM_CHAT_ID" \
            -d text="$message" \
            -d parse_mode=HTML > /dev/null 2>&1
    fi
}

# Check if log file exists
if [ ! -f "$LOG_FILE" ]; then
    send_alert "$(printf '🚨 <b>HEALTHCHECK FAILED</b>\nBot log file not found:\n<code>%s</code>' "$LOG_FILE")"
    exit 1
fi

# Check last modification time
if [ "$(uname)" = "Darwin" ]; then
    last_mod=$(stat -f %m "$LOG_FILE")
else
    last_mod=$(stat -c %Y "$LOG_FILE")
fi

now=$(date +%s)
age_minutes=$(( (now - last_mod) / 60 ))

if [ "$age_minutes" -gt "$MAX_AGE_MINUTES" ]; then
    send_alert "$(printf '🚨 <b>HEALTHCHECK FAILED</b>\nNo bot activity for <code>%d</code> minutes.\nLast log update: <code>%d</code> min ago.' "$age_minutes" "$age_minutes")"
    exit 1
fi
