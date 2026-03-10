#!/usr/bin/env bash
set -euo pipefail

# ── Crypto Bot Deploy Script ──
# Deploys the crypto trading bot to a Hetzner VPS via SSH + rsync.
# Idempotent: safe to run multiple times.
#
# Usage:
#   ./scripts/deploy.sh <server-ip> [--dry-run]
#
# Examples:
#   ./scripts/deploy.sh 65.21.100.50
#   ./scripts/deploy.sh myserver.example.com --dry-run

# ── Constants ──
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_USER="root"
REMOTE_DIR="/opt/bots/crypto-bot"
CRON_INTERVAL=15
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"

# ── Argument parsing ──
DRY_RUN=false
SERVER=""

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        -*)        echo "Unknown flag: $arg"; exit 1 ;;
        *)         SERVER="$arg" ;;
    esac
done

if [ -z "$SERVER" ]; then
    echo "Usage: $0 <server-ip-or-hostname> [--dry-run]"
    exit 1
fi

SSH_TARGET="${REMOTE_USER}@${SERVER}"

# ── Helpers ──
info()  { echo "==> $*"; }
warn()  { echo "WARNING: $*" >&2; }
die()   { echo "ERROR: $*" >&2; exit 1; }

run_remote() {
    if $DRY_RUN; then
        echo "[dry-run] ssh $SSH_TARGET: $1"
    else
        ssh $SSH_OPTS "$SSH_TARGET" "$1"
    fi
}

run_rsync() {
    local src="$1" dst="$2"
    shift 2
    if $DRY_RUN; then
        echo "[dry-run] rsync $src -> $SSH_TARGET:$dst"
        rsync -avz --dry-run -e "ssh $SSH_OPTS" "$@" "$src" "${SSH_TARGET}:${dst}" 2>/dev/null || true
    else
        rsync -avz -e "ssh $SSH_OPTS" "$@" "$src" "${SSH_TARGET}:${dst}"
    fi
}

# ── Pre-flight checks ──
info "Pre-flight checks"

if [ ! -f "$LOCAL_DIR/.env" ]; then
    die "Local .env file not found at $LOCAL_DIR/.env — create it before deploying."
fi

if ! command -v rsync &>/dev/null; then
    die "rsync is not installed locally."
fi

if ! command -v ssh &>/dev/null; then
    die "ssh is not installed locally."
fi

info "Target: $SSH_TARGET"
info "Remote dir: $REMOTE_DIR"
info "Local dir: $LOCAL_DIR"
$DRY_RUN && info "*** DRY RUN MODE — no changes will be made ***"
echo ""

# ── Step 1: Install system packages ──
info "Step 1/6: Installing system packages on server"
run_remote "export DEBIAN_FRONTEND=noninteractive && apt-get update -y -qq && apt-get install -y -qq python3 python3-venv python3-dev python3-pip sqlite3 rsync curl"

# ── Step 2: Sync project code ──
info "Step 2/6: Syncing project code to server"
run_remote "mkdir -p $REMOTE_DIR/logs $REMOTE_DIR/db"
run_rsync "$LOCAL_DIR/" "$REMOTE_DIR/" --exclude='.venv' --exclude='db/' --exclude='.env' --exclude='__pycache__' --exclude='.git' --exclude='data/' --exclude='*.pyc' --delete

# ── Step 3: Set up venv and install deps ──
info "Step 3/6: Setting up Python venv and dependencies"
run_remote "cd $REMOTE_DIR && if [ ! -d .venv ]; then python3 -m venv .venv && echo '    Created venv'; else echo '    Venv exists'; fi && .venv/bin/pip install --quiet --upgrade pip && .venv/bin/pip install --quiet -r requirements.txt && echo '    Dependencies installed'"

# ── Step 4: Sync .env and database ──
info "Step 4/6: Syncing .env and trade database"
run_rsync "$LOCAL_DIR/.env" "$REMOTE_DIR/.env"

if [ -f "$LOCAL_DIR/db/trading_bot.db" ]; then
    run_rsync "$LOCAL_DIR/db/trading_bot.db" "$REMOTE_DIR/db/trading_bot.db"
    info "Trade database synced"
else
    warn "Local db/trading_bot.db not found — skipping database sync."
fi

# ── Step 5: Set up cron job ──
info "Step 5/6: Configuring cron job (every ${CRON_INTERVAL} min)"
CRON_CMD="cd $REMOTE_DIR && $REMOTE_DIR/.venv/bin/python -m src.main >> $REMOTE_DIR/logs/bot.log 2>> $REMOTE_DIR/logs/cron_err.log"
run_remote "crontab -l 2>/dev/null | grep -v '$REMOTE_DIR' | crontab - 2>/dev/null || true; (crontab -l 2>/dev/null || true; echo '*/$CRON_INTERVAL * * * * $CRON_CMD') | crontab - && echo '    Cron installed:' && crontab -l | grep '$REMOTE_DIR'"

# ── Step 6: Set up UFW firewall ──
info "Step 6/6: Configuring UFW firewall"
run_remote "command -v ufw >/dev/null || apt-get install -y -qq ufw; ufw default deny incoming 2>/dev/null || true; ufw default allow outgoing 2>/dev/null || true; ufw allow ssh 2>/dev/null || true; ufw status | grep -q 'Status: active' || echo 'y' | ufw enable; echo '    UFW status:'; ufw status"

# ── Done ──
echo ""
echo "========================================"
info "Deployment complete!"
echo "========================================"
echo ""
echo "Server:    $SSH_TARGET"
echo "App dir:   $REMOTE_DIR"
echo "Cron:      every $CRON_INTERVAL min"
echo "Logs:      $REMOTE_DIR/logs/bot.log"
echo "Cron err:  $REMOTE_DIR/logs/cron_err.log"
echo ""
echo "Verify:"
echo "  ssh $SSH_TARGET 'crontab -l'"
echo "  ssh $SSH_TARGET 'tail -20 $REMOTE_DIR/logs/bot.log'"
echo ""
echo "Test run:"
echo "  ssh $SSH_TARGET 'cd $REMOTE_DIR && .venv/bin/python -m src.main'"
