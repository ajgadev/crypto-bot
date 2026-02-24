"""Unit tests for Binance filter rounding."""

from decimal import Decimal

from src.binance.filters import apply_lot_size, apply_price_filter, check_min_notional
from src.binance.types import SymbolFilters


def _filters(
    step: str = "0.00001",
    min_qty: str = "0.00001",
    tick: str = "0.01",
    min_notional: str = "10",
) -> SymbolFilters:
    return SymbolFilters(
        symbol="BTCUSDT",
        min_notional=Decimal(min_notional),
        lot_step_size=Decimal(step),
        lot_min_qty=Decimal(min_qty),
        lot_max_qty=Decimal("9999"),
        price_tick_size=Decimal(tick),
    )


class TestLotSize:
    def test_round_down(self) -> None:
        qty = Decimal("0.123456789")
        result = apply_lot_size(qty, _filters(step="0.00001"))
        assert result == Decimal("0.12345")

    def test_exact_step(self) -> None:
        qty = Decimal("1.00000")
        result = apply_lot_size(qty, _filters(step="0.00001"))
        assert result == Decimal("1.00000")

    def test_whole_number_step(self) -> None:
        qty = Decimal("3.7")
        result = apply_lot_size(qty, _filters(step="1", min_qty="1"))
        assert result == Decimal("3")


class TestPriceFilter:
    def test_round_to_tick(self) -> None:
        price = Decimal("50000.123")
        result = apply_price_filter(price, _filters(tick="0.01"))
        assert result == Decimal("50000.12")

    def test_exact_tick(self) -> None:
        price = Decimal("50000.10")
        result = apply_price_filter(price, _filters(tick="0.01"))
        assert result == Decimal("50000.10")


class TestMinNotional:
    def test_above_min(self) -> None:
        assert check_min_notional(
            Decimal("0.001"), Decimal("50000"), _filters(min_notional="10")
        )

    def test_below_min(self) -> None:
        assert not check_min_notional(
            Decimal("0.0001"), Decimal("50"), _filters(min_notional="10")
        )

    def test_exact_min(self) -> None:
        assert check_min_notional(
            Decimal("0.0002"), Decimal("50000"), _filters(min_notional="10")
        )
