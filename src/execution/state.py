"""SQLite state store for trades and idempotency keys."""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional

logger = logging.getLogger("crypto_bot")


@dataclass
class Trade:
    """In-memory representation of a trade row."""

    id: int
    symbol: str
    side: str
    status: str  # open, closed
    entry_price: Decimal
    entry_qty: Decimal
    entry_time: str
    exit_price: Optional[Decimal]
    exit_time: Optional[str]
    exit_reason: Optional[str]
    realized_pnl: Optional[Decimal]
    idempotency_key: str
    created_at: str
    updated_at: str
    strategy: str = "mean_reversion"
    highest_price: Optional[Decimal] = None


class StateStore:
    """SQLite-backed trade and idempotency state."""

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            db_dir = os.path.join(os.path.dirname(__file__), "..", "..", "db")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "trading_bot.db")
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        """Open connection and create tables."""
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        self._migrate()

    def close(self) -> None:
        if self._conn:
            self._conn.close()

    def _create_tables(self) -> None:
        assert self._conn is not None
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                entry_price TEXT NOT NULL,
                entry_qty TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                exit_price TEXT,
                exit_time TEXT,
                exit_reason TEXT,
                realized_pnl TEXT,
                idempotency_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                strategy TEXT NOT NULL DEFAULT 'mean_reversion',
                highest_price TEXT
            );
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS key_value (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        self._conn.commit()

    def _migrate(self) -> None:
        """Add strategy and highest_price columns to existing DBs."""
        assert self._conn is not None
        for col, col_def in [
            ("strategy", "TEXT NOT NULL DEFAULT 'mean_reversion'"),
            ("highest_price", "TEXT"),
        ]:
            try:
                self._conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_def}")
                self._conn.commit()
                logger.info("Migrated trades table: added column %s", col)
            except sqlite3.OperationalError:
                pass  # Column already exists

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── Idempotency ──

    def check_idempotency(self, key: str) -> bool:
        """Return True if key already exists (action already taken)."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT 1 FROM idempotency_keys WHERE key = ?", (key,)
        ).fetchone()
        return row is not None

    def record_idempotency(self, key: str) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT OR IGNORE INTO idempotency_keys (key, created_at) VALUES (?, ?)",
            (key, self._now()),
        )
        self._conn.commit()

    def cleanup_old_idempotency_keys(self) -> int:
        """Remove keys older than 24 hours. Returns count deleted."""
        assert self._conn is not None
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        cur = self._conn.execute(
            "DELETE FROM idempotency_keys WHERE created_at < ?", (cutoff,)
        )
        self._conn.commit()
        return cur.rowcount

    # ── Key-Value ──

    def get_kv(self, key: str) -> str | None:
        """Get a value by key, or None if not set."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT value FROM key_value WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_kv(self, key: str, value: str) -> None:
        """Upsert a key-value pair."""
        assert self._conn is not None
        self._conn.execute(
            "INSERT OR REPLACE INTO key_value (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    # ── Trades ──

    def get_open_trades(self, strategy: str | None = None) -> list[Trade]:
        assert self._conn is not None
        if strategy:
            rows = self._conn.execute(
                "SELECT * FROM trades WHERE status = 'open' AND strategy = ?",
                (strategy,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM trades WHERE status = 'open'"
            ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def get_open_trade_for_symbol(
        self, symbol: str, strategy: str | None = None
    ) -> Trade | None:
        assert self._conn is not None
        if strategy:
            row = self._conn.execute(
                "SELECT * FROM trades WHERE status = 'open' AND symbol = ? AND strategy = ? LIMIT 1",
                (symbol, strategy),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM trades WHERE status = 'open' AND symbol = ? LIMIT 1",
                (symbol,),
            ).fetchone()
        return self._row_to_trade(row) if row else None

    def insert_trade(
        self,
        symbol: str,
        side: str,
        entry_price: Decimal,
        entry_qty: Decimal,
        idempotency_key: str,
        strategy: str = "mean_reversion",
    ) -> int:
        assert self._conn is not None
        now = self._now()
        highest = str(entry_price) if strategy == "trend_follow" else None
        cur = self._conn.execute(
            """INSERT INTO trades
               (symbol, side, status, entry_price, entry_qty, entry_time,
                idempotency_key, created_at, updated_at, strategy, highest_price)
               VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                symbol, side, str(entry_price), str(entry_qty), now,
                idempotency_key, now, now, strategy, highest,
            ),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def close_trade(
        self,
        trade_id: int,
        exit_price: Decimal,
        exit_reason: str,
        realized_pnl: Decimal,
    ) -> None:
        assert self._conn is not None
        now = self._now()
        self._conn.execute(
            """UPDATE trades
               SET status='closed', exit_price=?, exit_time=?, exit_reason=?,
                   realized_pnl=?, updated_at=?
               WHERE id=?""",
            (str(exit_price), now, exit_reason, str(realized_pnl), now, trade_id),
        )
        self._conn.commit()

    def get_closed_trades_since(self, since_iso: str) -> list[Trade]:
        """Return trades closed after the given ISO timestamp."""
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE status='closed' AND exit_time >= ? ORDER BY exit_time",
            (since_iso,),
        ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def get_all_closed_trades(self) -> list[Trade]:
        """Return all closed trades."""
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE status='closed' ORDER BY exit_time",
        ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def update_highest_price(self, trade_id: int, price: Decimal) -> None:
        """Update the highest observed price for a trend_follow trade."""
        assert self._conn is not None
        now = self._now()
        self._conn.execute(
            """UPDATE trades SET highest_price = ?, updated_at = ?
               WHERE id = ? AND (highest_price IS NULL OR CAST(highest_price AS REAL) < ?)""",
            (str(price), now, trade_id, float(price)),
        )
        self._conn.commit()

    def _row_to_trade(self, row: sqlite3.Row) -> Trade:
        keys = row.keys()
        return Trade(
            id=row["id"],
            symbol=row["symbol"],
            side=row["side"],
            status=row["status"],
            entry_price=Decimal(row["entry_price"]),
            entry_qty=Decimal(row["entry_qty"]),
            entry_time=row["entry_time"],
            exit_price=Decimal(row["exit_price"]) if row["exit_price"] else None,
            exit_time=row["exit_time"],
            exit_reason=row["exit_reason"],
            realized_pnl=Decimal(row["realized_pnl"]) if row["realized_pnl"] else None,
            idempotency_key=row["idempotency_key"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            strategy=row["strategy"] if "strategy" in keys else "mean_reversion",
            highest_price=Decimal(row["highest_price"]) if "highest_price" in keys and row["highest_price"] else None,
        )
