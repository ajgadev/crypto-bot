"""Unit tests for position sizing math."""

from decimal import Decimal

from src.binance.types import SymbolFilters
from src.config.settings import Settings
from src.strategy.risk import compute_position_size


def _default_filters() -> SymbolFilters:
    return SymbolFilters(
        symbol="BTCUSDT",
        min_notional=Decimal("10"),
        lot_step_size=Decimal("0.00001"),
        lot_min_qty=Decimal("0.00001"),
        lot_max_qty=Decimal("9999"),
        price_tick_size=Decimal("0.01"),
    )


def _default_settings(**kwargs: object) -> Settings:
    return Settings(
        binance_api_key="test",
        binance_api_secret="test",
        **kwargs,  # type: ignore[arg-type]
    )


class TestPositionSizing:
    def test_basic_sizing(self) -> None:
        result = compute_position_size(
            current_price=Decimal("50000"),
            free_usdt=Decimal("1000"),
            equity_usdt=Decimal("1000"),
            slots_remaining=2,
            filters=_default_filters(),
            settings=_default_settings(),
        )
        assert result.can_trade
        assert result.quantity > 0
        assert result.notional > 0

    def test_no_budget(self) -> None:
        result = compute_position_size(
            current_price=Decimal("50000"),
            free_usdt=Decimal("10"),
            equity_usdt=Decimal("10"),
            slots_remaining=2,
            filters=_default_filters(),
            settings=_default_settings(),
        )
        assert not result.can_trade

    def test_no_slots(self) -> None:
        result = compute_position_size(
            current_price=Decimal("50000"),
            free_usdt=Decimal("1000"),
            equity_usdt=Decimal("1000"),
            slots_remaining=0,
            filters=_default_filters(),
            settings=_default_settings(),
        )
        assert not result.can_trade
        assert "slots" in result.skip_reason.lower()

    def test_respects_reserve(self) -> None:
        """Trade should not breach reserve."""
        result = compute_position_size(
            current_price=Decimal("50000"),
            free_usdt=Decimal("250"),
            equity_usdt=Decimal("1000"),
            slots_remaining=1,
            filters=_default_filters(),
            settings=_default_settings(reserve_pct=Decimal("0.20")),
        )
        if result.can_trade:
            remaining = Decimal("250") - result.notional
            reserve = max(Decimal("20"), Decimal("1000") * Decimal("0.20"))
            assert remaining >= reserve

    def test_below_min_notional(self) -> None:
        filters = _default_filters()
        filters.min_notional = Decimal("100000")
        result = compute_position_size(
            current_price=Decimal("50000"),
            free_usdt=Decimal("1000"),
            equity_usdt=Decimal("1000"),
            slots_remaining=2,
            filters=filters,
            settings=_default_settings(),
        )
        assert not result.can_trade

    def test_custom_tp_sl(self) -> None:
        """Settings with custom TP/SL should affect risk calc."""
        settings = _default_settings(
            take_profit_pct=Decimal("0.04"),
            stop_loss_pct=Decimal("0.03"),
        )
        assert settings.tp_multiplier == Decimal("1.04")
        assert settings.sl_multiplier == Decimal("0.97")
