"""Unit tests for RSI, EMA, and percent change indicators."""

from decimal import Decimal

import pytest

from src.indicators.ema import compute_ema
from src.indicators.percent_change import compute_pct_change_24h
from src.indicators.rsi import compute_rsi


class TestRSI:
    def test_rsi_all_gains(self) -> None:
        """RSI should be 100 when all moves are gains."""
        closes = [Decimal(str(i)) for i in range(1, 20)]
        rsi = compute_rsi(closes, 14)
        assert rsi == Decimal("100")

    def test_rsi_all_losses(self) -> None:
        """RSI should be 0 when all moves are losses."""
        closes = [Decimal(str(20 - i)) for i in range(20)]
        rsi = compute_rsi(closes, 14)
        assert rsi == Decimal("0")

    def test_rsi_mixed(self) -> None:
        """RSI should be between 0 and 100 for mixed data."""
        closes = [Decimal(str(x)) for x in [
            44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
            46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03,
        ]]
        rsi = compute_rsi(closes, 14)
        assert Decimal("0") < rsi < Decimal("100")

    def test_rsi_insufficient_data(self) -> None:
        with pytest.raises(ValueError, match="Need at least"):
            compute_rsi([Decimal("1")] * 10, 14)


class TestEMA:
    def test_ema_constant_price(self) -> None:
        """EMA of constant prices equals that price."""
        closes = [Decimal("100")] * 30
        ema = compute_ema(closes, 9)
        assert ema == Decimal("100")

    def test_ema_trending_up(self) -> None:
        """EMA of uptrending prices should be above SMA."""
        closes = [Decimal(str(i)) for i in range(1, 30)]
        ema9 = compute_ema(closes, 9)
        sma = sum(closes[-9:]) / 9
        # EMA should react faster, be closer to recent prices
        assert ema9 > sma - Decimal("5")

    def test_ema9_vs_ema21_bullish(self) -> None:
        """In uptrend, EMA9 > EMA21."""
        closes = [Decimal(str(i * 2)) for i in range(1, 30)]
        ema9 = compute_ema(closes, 9)
        ema21 = compute_ema(closes, 21)
        assert ema9 > ema21

    def test_ema_insufficient_data(self) -> None:
        with pytest.raises(ValueError, match="Need at least"):
            compute_ema([Decimal("1")] * 5, 9)


class TestPctChange:
    def test_no_change(self) -> None:
        closes = [Decimal("100")] * 30
        result = compute_pct_change_24h(closes)
        assert result == Decimal("0")

    def test_positive_change(self) -> None:
        closes = [Decimal("100")] * 25
        closes[-1] = Decimal("110")
        result = compute_pct_change_24h(closes)
        assert result == Decimal("0.1")

    def test_negative_change(self) -> None:
        closes = [Decimal("100")] * 25
        closes[-1] = Decimal("90")
        result = compute_pct_change_24h(closes)
        assert result == Decimal("-0.1")

    def test_insufficient_data(self) -> None:
        with pytest.raises(ValueError, match="Need at least 25"):
            compute_pct_change_24h([Decimal("100")] * 20)
