"""Parameter sweep — reduced grid, flushed output."""

from __future__ import annotations

import itertools
from decimal import Decimal

from src.backtest.data_loader import load_csv
from src.backtest.engine import run_backtest
import src.backtest.engine as eng_mod
import src.strategy.signals as sig


def _run_one(symbols, settings, patch_fn=None):
    """Run one backtest, optionally patching MR entry."""
    orig_sig = sig.check_entry_signal
    orig_eng = eng_mod.check_entry_signal
    if patch_fn:
        sig.check_entry_signal = patch_fn
        eng_mod.check_entry_signal = patch_fn
    try:
        result = run_backtest(symbols, settings=settings)
    finally:
        sig.check_entry_signal = orig_sig
        eng_mod.check_entry_signal = orig_eng

    ret = float((result.final_equity / result.initial_capital - 1) * 100)
    max_dd = 0.0
    if result.equity_curve:
        peak = float(result.equity_curve[0])
        for eq in result.equity_curve:
            eq_f = float(eq)
            if eq_f > peak:
                peak = eq_f
            dd = (peak - eq_f) / peak * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
    wins = sum(1 for t in result.trades if t.pnl_usdt > 0)
    wr = (wins / len(result.trades) * 100) if result.trades else 0.0
    mr_n = sum(1 for t in result.trades if t.strategy == "mean_reversion")
    tf_n = sum(1 for t in result.trades if t.strategy == "trend_follow")
    return {
        "ret": ret, "trades": len(result.trades), "wr": wr,
        "dd": max_dd, "equity": float(result.final_equity),
        "mr_trades": mr_n, "tf_trades": tf_n,
    }


def _make_patch(rsi_t, pct_t):
    def patched(indicators, has_open_trade, slots_remaining, tradable_usdt):
        from src.strategy.signals import EntrySignal
        if indicators.ema_short <= indicators.ema_long:
            return EntrySignal(False, "Bearish", indicators)
        if indicators.pct_change_24h > pct_t:
            return EntrySignal(False, "Pct", indicators)
        if indicators.rsi >= rsi_t:
            return EntrySignal(False, "RSI", indicators)
        if has_open_trade:
            return EntrySignal(False, "Open", indicators)
        if slots_remaining <= 0:
            return EntrySignal(False, "Slots", indicators)
        if tradable_usdt <= 0:
            return EntrySignal(False, "Budget", indicators)
        return EntrySignal(True, "OK", indicators)
    return patched


def p(msg):
    print(msg, flush=True)


def main():
    symbols = {
        "BTCUSDC": load_csv("data/btcusdc_1h.csv"),
        "ETHUSDC": load_csv("data/ethusdc_1h.csv"),
        "BNBUSDC": load_csv("data/bnbusdc_1h.csv"),
        "SOLUSDC": load_csv("data/solusdc_1h.csv"),
    }
    p(f"Loaded {sum(len(v) for v in symbols.values())} klines\n")

    # ── PHASE 1: Mean-Reversion (54 combos) ──
    p("=" * 70)
    p("PHASE 1: Mean-Reversion Sweep (TF off)")
    p("=" * 70)

    rsi_vals = [Decimal("40"), Decimal("45"), Decimal("50")]
    pct_vals = [Decimal("-0.01"), Decimal("-0.02"), Decimal("-0.03")]
    tp_vals = [Decimal("0.04"), Decimal("0.06")]
    sl_vals = [Decimal("0.03"), Decimal("0.04"), Decimal("0.05")]

    mr_results = []
    total = len(rsi_vals) * len(pct_vals) * len(tp_vals) * len(sl_vals)
    p(f"Testing {total} combos...")

    for i, (rsi_t, pct_t, tp, sl) in enumerate(
        itertools.product(rsi_vals, pct_vals, tp_vals, sl_vals), 1
    ):
        if i % 10 == 0:
            p(f"  {i}/{total}")

        settings = Settings(
            binance_api_key="t", binance_api_secret="t", run_mode="backtest",
            mean_reversion_enabled=True, trend_follow_enabled=False,
            take_profit_pct=tp, stop_loss_pct=sl,
        )
        r = _run_one(symbols, settings, _make_patch(rsi_t, pct_t))
        r.update({"rsi": rsi_t, "pct": pct_t, "tp": tp, "sl": sl})
        mr_results.append(r)

    mr_results.sort(key=lambda x: x["ret"], reverse=True)
    p(f"\n{'─' * 70}")
    p("TOP 15 MEAN-REVERSION:")
    p(f"{'─' * 70}")
    p(f"{'RSI<':>6} {'Pct≤':>7} {'TP%':>5} {'SL%':>5} {'Return%':>9} {'Trades':>7} {'WinR%':>7} {'MaxDD%':>7} {'Equity':>10}")
    for r in mr_results[:15]:
        p(f"{r['rsi']:>6} {r['pct']:>7} {r['tp']:>5} {r['sl']:>5} {r['ret']:>9.2f} {r['trades']:>7} {r['wr']:>7.1f} {r['dd']:>7.2f} {r['equity']:>10.2f}")

    # ── PHASE 2: Trend-Follow (36 combos) ──
    p(f"\n{'=' * 70}")
    p("PHASE 2: Trend-Follow Sweep (MR off)")
    p("=" * 70)

    trails = [Decimal("0.05"), Decimal("0.08"), Decimal("0.10"), Decimal("0.15")]
    vols = [Decimal("1.5"), Decimal("2.0"), Decimal("3.0")]
    rsi_ranges = [
        (Decimal("45"), Decimal("65")),
        (Decimal("50"), Decimal("70")),
        (Decimal("55"), Decimal("75")),
    ]

    tf_results = []
    total = len(trails) * len(vols) * len(rsi_ranges)
    p(f"Testing {total} combos...")

    for i, (trail, vol, (rsi_min, rsi_max)) in enumerate(
        itertools.product(trails, vols, rsi_ranges), 1
    ):
        if i % 5 == 0:
            p(f"  {i}/{total}")

        settings = Settings(
            binance_api_key="t", binance_api_secret="t", run_mode="backtest",
            mean_reversion_enabled=False, trend_follow_enabled=True,
            trend_follow_trailing_stop_pct=trail,
            trend_follow_volume_multiplier=vol,
            trend_follow_rsi_min=rsi_min, trend_follow_rsi_max=rsi_max,
        )
        r = _run_one(symbols, settings)
        r.update({"trail": trail, "vol": vol, "rsi_min": rsi_min, "rsi_max": rsi_max})
        tf_results.append(r)

    tf_results.sort(key=lambda x: x["ret"], reverse=True)
    p(f"\n{'─' * 70}")
    p("TOP 15 TREND-FOLLOW:")
    p(f"{'─' * 70}")
    p(f"{'Trail%':>7} {'VolX':>5} {'RSI':>9} {'Return%':>9} {'Trades':>7} {'WinR%':>7} {'MaxDD%':>7} {'Equity':>10}")
    for r in tf_results[:15]:
        rsi_s = f"{r['rsi_min']}-{r['rsi_max']}"
        p(f"{r['trail']:>7} {r['vol']:>5} {rsi_s:>9} {r['ret']:>9.2f} {r['trades']:>7} {r['wr']:>7.1f} {r['dd']:>7.2f} {r['equity']:>10.2f}")

    # ── PHASE 3: Best combined ──
    best_mr = mr_results[0] if mr_results and mr_results[0]["trades"] > 0 else None
    best_tf = tf_results[0] if tf_results and tf_results[0]["trades"] > 0 else None

    if best_mr or best_tf:
        p(f"\n{'=' * 70}")
        p("PHASE 3: Best Combined Run")
        p("=" * 70)

        kwargs = {
            "binance_api_key": "t", "binance_api_secret": "t", "run_mode": "backtest",
            "mean_reversion_enabled": best_mr is not None,
            "trend_follow_enabled": best_tf is not None,
        }
        patch = None
        if best_mr:
            kwargs["take_profit_pct"] = best_mr["tp"]
            kwargs["stop_loss_pct"] = best_mr["sl"]
            patch = _make_patch(best_mr["rsi"], best_mr["pct"])
            p(f"MR: RSI<{best_mr['rsi']} Pct≤{best_mr['pct']} TP={best_mr['tp']} SL={best_mr['sl']} (solo: {best_mr['ret']:.2f}%)")

        if best_tf:
            kwargs["trend_follow_trailing_stop_pct"] = best_tf["trail"]
            kwargs["trend_follow_volume_multiplier"] = best_tf["vol"]
            kwargs["trend_follow_rsi_min"] = best_tf["rsi_min"]
            kwargs["trend_follow_rsi_max"] = best_tf["rsi_max"]
            p(f"TF: Trail={best_tf['trail']} Vol={best_tf['vol']} RSI={best_tf['rsi_min']}-{best_tf['rsi_max']} (solo: {best_tf['ret']:.2f}%)")

        settings = Settings(**kwargs)
        r = _run_one(symbols, settings, patch)
        p(f"\nReturn:     {r['ret']:.2f}%")
        p(f"Equity:     ${r['equity']:.2f}")
        p(f"Trades:     {r['trades']} (MR: {r['mr_trades']}, TF: {r['tf_trades']})")
        p(f"Win Rate:   {r['wr']:.1f}%")
        p(f"Max DD:     {r['dd']:.2f}%")

    p("\nDone.")


if __name__ == "__main__":
    from src.config.settings import Settings
    main()
