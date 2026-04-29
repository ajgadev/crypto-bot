"""Pre/post strategy-change comparison report.

Computes per-strategy and per-coin PnL, win rate, and exit-reason breakdown for
trades closed before vs after a cutoff date. Sends formatted message via Telegram.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from src.execution.state import StateStore


@dataclass
class Bucket:
    n: int = 0
    wins: int = 0
    pnl: Decimal = Decimal("0")
    by_symbol: dict[str, tuple[int, int, Decimal]] = None  # type: ignore[assignment]
    by_exit: dict[str, tuple[int, Decimal]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.by_symbol is None:
            self.by_symbol = {}
        if self.by_exit is None:
            self.by_exit = {}

    @property
    def win_rate(self) -> float:
        return (self.wins / self.n * 100) if self.n else 0.0


def _bucketize(rows: list[tuple]) -> dict[str, Bucket]:
    """Group rows (strategy, symbol, exit_reason, pnl) into Buckets per strategy."""
    out: dict[str, Bucket] = defaultdict(Bucket)
    sym_acc: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    exit_acc: dict[tuple[str, str], list[Decimal]] = defaultdict(list)

    for strat, sym, reason, pnl_str in rows:
        pnl = Decimal(pnl_str) if pnl_str else Decimal("0")
        b = out[strat]
        b.n += 1
        b.pnl += pnl
        if pnl > 0:
            b.wins += 1
        sym_acc[(strat, sym)].append(pnl)
        exit_acc[(strat, reason or "UNKNOWN")].append(pnl)

    for (strat, sym), pnls in sym_acc.items():
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        out[strat].by_symbol[sym] = (n, wins, sum(pnls, Decimal("0")))
    for (strat, reason), pnls in exit_acc.items():
        out[strat].by_exit[reason] = (len(pnls), sum(pnls, Decimal("0")))
    return out


def _query(conn: sqlite3.Connection, *, before: str | None = None,
           after: str | None = None) -> list[tuple]:
    sql = "SELECT strategy, symbol, exit_reason, realized_pnl FROM trades WHERE status='closed'"
    params: list[str] = []
    if before is not None:
        sql += " AND entry_time < ?"
        params.append(before)
    if after is not None:
        sql += " AND entry_time >= ?"
        params.append(after)
    cur = conn.execute(sql, params)
    return cur.fetchall()


def build_comparison(
    db_path: str,
    cutoff_iso: str,
) -> dict[str, dict[str, Bucket]]:
    """Return {'pre': {...}, 'post': {...}} bucketed by strategy."""
    conn = sqlite3.connect(db_path)
    try:
        pre = _bucketize(_query(conn, before=cutoff_iso))
        post = _bucketize(_query(conn, after=cutoff_iso))
    finally:
        conn.close()
    return {"pre": pre, "post": post}


def _fmt_pnl(d: Decimal) -> str:
    return f"{float(d):+.2f}"


def format_telegram(
    comparison: dict[str, dict[str, Bucket]],
    cutoff_iso: str,
) -> str:
    """Render a Telegram-friendly message with short summary + detailed breakdown."""
    cutoff_short = cutoff_iso[:10]
    pre = comparison["pre"]
    post = comparison["post"]

    strategies = sorted(set(pre) | set(post))
    lines: list[str] = []
    lines.append(f"*Strategy change report — pre/post {cutoff_short}*")
    lines.append("")

    # ── Short summary table ──
    lines.append("```")
    lines.append(f"{'Strategy':<14} {'PRE n  pnl':>16}  {'POST n  pnl':>16}  Δpnl")
    for strat in strategies:
        p = pre.get(strat, Bucket())
        q = post.get(strat, Bucket())
        delta = q.pnl - p.pnl
        lines.append(
            f"{strat:<14} {p.n:>3} {_fmt_pnl(p.pnl):>10}  "
            f"{q.n:>3} {_fmt_pnl(q.pnl):>10}  {_fmt_pnl(delta):>8}"
        )
    lines.append("```")

    # ── Detailed breakdown per strategy ──
    for strat in strategies:
        p = pre.get(strat, Bucket())
        q = post.get(strat, Bucket())
        lines.append("")
        lines.append(f"*{strat}*")
        lines.append(
            f"  pre  : n={p.n} W/L={p.wins}/{p.n - p.wins} "
            f"win={p.win_rate:.0f}% pnl={_fmt_pnl(p.pnl)}"
        )
        lines.append(
            f"  post : n={q.n} W/L={q.wins}/{q.n - q.wins} "
            f"win={q.win_rate:.0f}% pnl={_fmt_pnl(q.pnl)}"
        )

        # Per-symbol
        all_syms = sorted(set(p.by_symbol) | set(q.by_symbol))
        if all_syms:
            lines.append("  by symbol:")
            for sym in all_syms:
                pn, pw, pp = p.by_symbol.get(sym, (0, 0, Decimal("0")))
                qn, qw, qp = q.by_symbol.get(sym, (0, 0, Decimal("0")))
                lines.append(
                    f"    {sym:<8} pre {pn:>2} {_fmt_pnl(pp):>8}  "
                    f"post {qn:>2} {_fmt_pnl(qp):>8}"
                )

        # Exit reasons
        all_exits = sorted(set(p.by_exit) | set(q.by_exit))
        if all_exits:
            lines.append("  exits:")
            for reason in all_exits:
                pn, pp = p.by_exit.get(reason, (0, Decimal("0")))
                qn, qp = q.by_exit.get(reason, (0, Decimal("0")))
                lines.append(
                    f"    {reason:<14} pre {pn:>2} {_fmt_pnl(pp):>8}  "
                    f"post {qn:>2} {_fmt_pnl(qp):>8}"
                )

    return "\n".join(lines)


# ── Idempotent send guards (mirror AI daily report) ──

def should_send_strategy_comparison(
    state: StateStore,
    target_date_iso: str,
) -> bool:
    """Return True if today is on/after target_date and report hasn't been sent yet."""
    if not target_date_iso:
        return False
    try:
        target = datetime.fromisoformat(target_date_iso).date()
    except ValueError:
        return False
    today = datetime.now(timezone.utc).date()
    if today < target:
        return False
    return state.get_kv("strategy_comparison_sent") is None


def mark_strategy_comparison_sent(state: StateStore) -> None:
    state.set_kv(
        "strategy_comparison_sent",
        datetime.now(timezone.utc).isoformat(),
    )
