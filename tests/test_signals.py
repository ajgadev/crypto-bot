"""Unit tests for entry/exit signal logic."""

from decimal import Decimal

from src.config.settings import Settings
from src.strategy.signals import (
    Indicators,
    check_defensive_mode,
    check_entry_signal,
    check_exit_signal,
    check_trend_follow_entry,
    check_trend_follow_exit,
)


def _bullish_indicators(**kwargs: object) -> Indicators:
    """Default indicators meeting all mean-reversion entry conditions."""
    defaults = {
        "rsi": Decimal("30"),
        "ema_short": Decimal("100"),
        "ema_long": Decimal("95"),
        "pct_change_24h": Decimal("-0.05"),
        "last_close": Decimal("100"),
    }
    defaults.update(kwargs)  # type: ignore[arg-type]
    return Indicators(**defaults)  # type: ignore[arg-type]


def _tf_indicators(**kwargs: object) -> Indicators:
    """Default indicators meeting all trend-follow entry conditions.

    Default: crossover happened 1 candle ago (EMA short went from 99->102 while EMA long stayed 100).
    """
    defaults: dict[str, object] = {
        "rsi": Decimal("60"),
        "ema_short": Decimal("102"),
        "ema_long": Decimal("100"),
        "pct_change_24h": Decimal("0.01"),
        "last_close": Decimal("102"),
        "ema_short_history": [Decimal("99")],
        "ema_long_history": [Decimal("100")],
        "current_volume": Decimal("1500"),
        "avg_volume": Decimal("800"),
    }
    defaults.update(kwargs)  # type: ignore[arg-type]
    return Indicators(**defaults)  # type: ignore[arg-type]


def _settings(**kwargs: object) -> Settings:
    return Settings(
        binance_api_key="test",
        binance_api_secret="test",
        **kwargs,  # type: ignore[arg-type]
    )


class TestEntrySignal:
    def test_all_conditions_met(self) -> None:
        signal = check_entry_signal(
            _bullish_indicators(),
            has_open_trade=False,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
        )
        assert signal.should_enter

    def test_bearish_bias_blocks(self) -> None:
        signal = check_entry_signal(
            _bullish_indicators(ema_short=Decimal("90"), ema_long=Decimal("95")),
            has_open_trade=False,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
        )
        assert not signal.should_enter
        assert "Bearish" in signal.reason

    def test_insufficient_drop_blocks(self) -> None:
        """Pct change above threshold (default -0.01) should block."""
        signal = check_entry_signal(
            _bullish_indicators(pct_change_24h=Decimal("0.00")),
            has_open_trade=False,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
        )
        assert not signal.should_enter

    def test_high_rsi_blocks(self) -> None:
        """RSI at or above threshold (default 50) should block."""
        signal = check_entry_signal(
            _bullish_indicators(rsi=Decimal("55")),
            has_open_trade=False,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
        )
        assert not signal.should_enter

    def test_existing_trade_blocks(self) -> None:
        signal = check_entry_signal(
            _bullish_indicators(),
            has_open_trade=True,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
        )
        assert not signal.should_enter

    def test_no_slots_blocks(self) -> None:
        signal = check_entry_signal(
            _bullish_indicators(),
            has_open_trade=False,
            slots_remaining=0,
            tradable_usdt=Decimal("500"),
        )
        assert not signal.should_enter

    def test_no_budget_blocks(self) -> None:
        signal = check_entry_signal(
            _bullish_indicators(),
            has_open_trade=False,
            slots_remaining=2,
            tradable_usdt=Decimal("0"),
        )
        assert not signal.should_enter


class TestExitSignal:
    def test_take_profit(self) -> None:
        """TP at 4% (configurable)."""
        settings = _settings(take_profit_pct=Decimal("0.04"))
        signal = check_exit_signal(
            entry_price=Decimal("100"),
            current_price=Decimal("104.01"),
            rsi=Decimal("50"),
            settings=settings,
        )
        assert signal.should_exit
        assert signal.reason == "TP"

    def test_stop_loss(self) -> None:
        """SL at 3% (configurable)."""
        settings = _settings(stop_loss_pct=Decimal("0.03"))
        signal = check_exit_signal(
            entry_price=Decimal("100"),
            current_price=Decimal("96.99"),
            rsi=Decimal("50"),
            settings=settings,
        )
        assert signal.should_exit
        assert signal.reason == "SL"

    def test_rsi_exit(self) -> None:
        settings = _settings()
        signal = check_exit_signal(
            entry_price=Decimal("100"),
            current_price=Decimal("101"),
            rsi=Decimal("71"),
            settings=settings,
        )
        assert signal.should_exit
        assert signal.reason == "RSI_EXIT"

    def test_rsi_exit_custom_threshold(self) -> None:
        """RSI exit respects configurable threshold."""
        settings = _settings(mean_reversion_rsi_exit=Decimal("70"))
        # RSI 66 should NOT trigger exit with threshold at 70
        signal = check_exit_signal(
            entry_price=Decimal("100"),
            current_price=Decimal("101"),
            rsi=Decimal("66"),
            settings=settings,
        )
        assert not signal.should_exit
        # RSI 71 SHOULD trigger exit with threshold at 70
        signal = check_exit_signal(
            entry_price=Decimal("100"),
            current_price=Decimal("101"),
            rsi=Decimal("71"),
            settings=settings,
        )
        assert signal.should_exit
        assert signal.reason == "RSI_EXIT"

    def test_no_exit(self) -> None:
        settings = _settings()
        signal = check_exit_signal(
            entry_price=Decimal("100"),
            current_price=Decimal("101"),
            rsi=Decimal("50"),
            settings=settings,
        )
        assert not signal.should_exit

    def test_custom_tp_sl(self) -> None:
        """Verify custom TP/SL values work."""
        settings = _settings(
            take_profit_pct=Decimal("0.10"),
            stop_loss_pct=Decimal("0.05"),
        )
        # Should NOT trigger TP at 4%
        signal = check_exit_signal(
            entry_price=Decimal("100"),
            current_price=Decimal("104"),
            rsi=Decimal("50"),
            settings=settings,
        )
        assert not signal.should_exit

        # SHOULD trigger TP at 10%
        signal = check_exit_signal(
            entry_price=Decimal("100"),
            current_price=Decimal("110.01"),
            rsi=Decimal("50"),
            settings=settings,
        )
        assert signal.should_exit
        assert signal.reason == "TP"


class TestTrendFollowEntry:
    def test_all_conditions_met(self) -> None:
        settings = _settings()
        signal = check_trend_follow_entry(
            _tf_indicators(),
            has_open_trade=False,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
            settings=settings,
        )
        assert signal.should_enter

    def test_no_crossover_blocks(self) -> None:
        """If all candles in window were already bullish, no fresh crossover."""
        settings = _settings()
        signal = check_trend_follow_entry(
            _tf_indicators(
                ema_short_history=[Decimal("101"), Decimal("101.5"), Decimal("101.8")],
                ema_long_history=[Decimal("100"), Decimal("100"), Decimal("100")],
            ),
            has_open_trade=False,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
            settings=settings,
        )
        assert not signal.should_enter
        assert "crossover" in signal.reason.lower()

    def test_crossover_2_candles_ago_triggers(self) -> None:
        """Crossover happened 2 candles ago — still within default window=3."""
        settings = _settings(trend_follow_crossover_window=3)
        signal = check_trend_follow_entry(
            _tf_indicators(
                ema_short_history=[Decimal("99"), Decimal("101"), Decimal("101.5")],
                ema_long_history=[Decimal("100"), Decimal("100"), Decimal("100")],
            ),
            has_open_trade=False,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
            settings=settings,
        )
        assert signal.should_enter

    def test_crossover_outside_window_blocks(self) -> None:
        """Crossover happened 4 candles ago — outside window=3, should block."""
        settings = _settings(trend_follow_crossover_window=3)
        # History has 4 candles; only first is bearish (outside window of 3)
        signal = check_trend_follow_entry(
            _tf_indicators(
                ema_short_history=[Decimal("99"), Decimal("101"), Decimal("101.5"), Decimal("102")],
                ema_long_history=[Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100")],
            ),
            has_open_trade=False,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
            settings=settings,
        )
        assert not signal.should_enter
        assert "crossover" in signal.reason.lower()

    def test_rsi_too_low_blocks(self) -> None:
        settings = _settings()
        signal = check_trend_follow_entry(
            _tf_indicators(rsi=Decimal("40")),
            has_open_trade=False,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
            settings=settings,
        )
        assert not signal.should_enter

    def test_rsi_too_high_blocks(self) -> None:
        settings = _settings(trend_follow_rsi_max=Decimal("70"))
        signal = check_trend_follow_entry(
            _tf_indicators(rsi=Decimal("75")),
            has_open_trade=False,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
            settings=settings,
        )
        assert not signal.should_enter

    def test_low_volume_blocks(self) -> None:
        settings = _settings()
        signal = check_trend_follow_entry(
            _tf_indicators(current_volume=Decimal("900")),
            has_open_trade=False,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
            settings=settings,
        )
        assert not signal.should_enter
        assert "Volume" in signal.reason

    def test_no_ema_history_blocks(self) -> None:
        settings = _settings()
        signal = check_trend_follow_entry(
            _tf_indicators(ema_short_history=None, ema_long_history=None),
            has_open_trade=False,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
            settings=settings,
        )
        assert not signal.should_enter

    def test_existing_trade_blocks(self) -> None:
        settings = _settings()
        signal = check_trend_follow_entry(
            _tf_indicators(),
            has_open_trade=True,
            slots_remaining=2,
            tradable_usdt=Decimal("500"),
            settings=settings,
        )
        assert not signal.should_enter

    def test_no_slots_blocks(self) -> None:
        settings = _settings()
        signal = check_trend_follow_entry(
            _tf_indicators(),
            has_open_trade=False,
            slots_remaining=0,
            tradable_usdt=Decimal("500"),
            settings=settings,
        )
        assert not signal.should_enter


class TestTrendFollowExit:
    def test_trailing_stop(self) -> None:
        settings = _settings(trend_follow_trailing_stop_pct=Decimal("0.05"))
        indicators = _tf_indicators(ema_short=Decimal("102"), ema_long=Decimal("100"))
        signal = check_trend_follow_exit(
            entry_price=Decimal("100"),
            highest_price=Decimal("110"),
            current_price=Decimal("104"),  # dropped > 5% from 110
            indicators=indicators,
            settings=settings,
        )
        assert signal.should_exit
        assert signal.reason == "TRAILING_STOP"

    def test_death_cross(self) -> None:
        settings = _settings()
        indicators = _tf_indicators(ema_short=Decimal("99"), ema_long=Decimal("100"))
        signal = check_trend_follow_exit(
            entry_price=Decimal("100"),
            highest_price=Decimal("105"),
            current_price=Decimal("103"),  # not trailing stop
            indicators=indicators,
            settings=settings,
        )
        assert signal.should_exit
        assert signal.reason == "DEATH_CROSS"

    def test_no_exit(self) -> None:
        settings = _settings()
        indicators = _tf_indicators(ema_short=Decimal("102"), ema_long=Decimal("100"))
        signal = check_trend_follow_exit(
            entry_price=Decimal("100"),
            highest_price=Decimal("105"),
            current_price=Decimal("103"),  # within 5% of peak, still bullish
            indicators=indicators,
            settings=settings,
        )
        assert not signal.should_exit


class TestDefensiveMode:
    def test_bear_when_close_below_ema(self) -> None:
        """Close below EMA200 should return True (bear)."""
        # Create declining closes that put the last close below EMA200
        closes = [Decimal("100")] * 200 + [Decimal("90")]
        settings = _settings(defensive_mode_enabled=True, defensive_mode_ema=200)
        assert check_defensive_mode(closes, settings) is True

    def test_bull_when_close_above_ema(self) -> None:
        """Close above EMA200 should return False (not bear)."""
        # Create rising closes that keep last close above EMA200
        closes = [Decimal("100")] * 200 + [Decimal("110")]
        settings = _settings(defensive_mode_enabled=True, defensive_mode_ema=200)
        assert check_defensive_mode(closes, settings) is False

    def test_disabled_returns_false(self) -> None:
        """When disabled, should always return False regardless of price."""
        closes = [Decimal("100")] * 200 + [Decimal("50")]
        settings = _settings(defensive_mode_enabled=False, defensive_mode_ema=200)
        assert check_defensive_mode(closes, settings) is False

    def test_not_enough_data_returns_false(self) -> None:
        """With insufficient data for EMA, should return False."""
        closes = [Decimal("100")] * 50
        settings = _settings(defensive_mode_enabled=True, defensive_mode_ema=200)
        assert check_defensive_mode(closes, settings) is False
