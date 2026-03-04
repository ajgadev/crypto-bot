#!/usr/bin/env bash
set -euo pipefail

# ── Crypto Bot Setup Script ──
# Sets up venv, installs deps, creates .env, and configures cron.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
CRON_INTERVAL="${1:-15}"  # minutes, default 15

echo "==> Setting up crypto-bot in $SCRIPT_DIR"

# ── Python check ──
PYTHON=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major="${version%%.*}"
        minor="${version##*.}"
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "==> Python 3.11+ not found, attempting to install..."

    if command -v apt-get &>/dev/null; then
        # Debian/Ubuntu
        sudo apt-get update -y
        sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
        PYTHON="python3.12"
    elif command -v dnf &>/dev/null; then
        # Fedora/RHEL
        sudo dnf install -y python3.12 python3.12-devel
        PYTHON="python3.12"
    elif command -v yum &>/dev/null; then
        # Older RHEL/CentOS
        sudo yum install -y python3.12 python3.12-devel
        PYTHON="python3.12"
    elif command -v brew &>/dev/null; then
        # macOS
        brew install python@3.12
        PYTHON="python3.12"
    else
        echo "ERROR: Could not install Python automatically."
        echo "Please install Python 3.11+ manually and re-run this script."
        exit 1
    fi

    if ! command -v "$PYTHON" &>/dev/null; then
        echo "ERROR: Python installation failed."
        exit 1
    fi
fi
echo "==> Using $PYTHON ($($PYTHON --version))"

# ── Virtual environment ──
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
else
    echo "==> Virtual environment already exists"
fi

echo "==> Installing dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"

# ── Directories ──
mkdir -p "$SCRIPT_DIR/logs" "$SCRIPT_DIR/db"

# ── .env file ──
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "==> Created .env from .env.example — edit it with your credentials"
else
    echo "==> .env already exists, skipping"
fi

# ── Cron setup ──
# stdout goes to /dev/null (bot.log already captures everything via RotatingFileHandler).
# stderr goes to cron_err.log to catch unexpected crashes before the logger initializes.
CRON_CMD="cd $SCRIPT_DIR && $VENV_DIR/bin/python -m src.main > /dev/null 2>> $SCRIPT_DIR/logs/cron_err.log"

if crontab -l 2>/dev/null | grep -qF "$SCRIPT_DIR"; then
    echo "==> Cron job already exists. Current entry:"
    crontab -l | grep -F "$SCRIPT_DIR"
    read -rp "    Replace it? [y/N] " answer
    if [[ "$answer" =~ ^[Yy]$ ]]; then
        crontab -l 2>/dev/null | grep -vF "$SCRIPT_DIR" | crontab -
    else
        echo "==> Keeping existing cron job"
        echo ""
        echo "Setup complete!"
        exit 0
    fi
fi

(crontab -l 2>/dev/null; echo "*/$CRON_INTERVAL * * * * $CRON_CMD") | crontab -
echo "==> Cron job installed: every $CRON_INTERVAL minutes"

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your Binance API keys and Telegram credentials"
echo "  2. Verify cron: crontab -l"
echo "  3. Check logs: tail -f $SCRIPT_DIR/logs/cron.log"
