"""Unit tests for volume indicators."""

from decimal import Decimal

import pytest

from src.indicators.volume import compute_volume_sma, is_volume_confirmed


class TestVolumeSMA:
    def test_constant_volume(self) -> None:
        volumes = [Decimal("1000")] * 20
        result = compute_volume_sma(volumes, 20)
        assert result == Decimal("1000")

    def test_uses_last_n_values(self) -> None:
        volumes = [Decimal("100")] * 10 + [Decimal("200")] * 20
        result = compute_volume_sma(volumes, 20)
        assert result == Decimal("200")

    def test_insufficient_data(self) -> None:
        with pytest.raises(ValueError, match="Need at least"):
            compute_volume_sma([Decimal("100")] * 5, 20)

    def test_mixed_volumes(self) -> None:
        volumes = [Decimal(str(i * 100)) for i in range(1, 21)]
        result = compute_volume_sma(volumes, 20)
        expected = sum(Decimal(str(i * 100)) for i in range(1, 21)) / Decimal("20")
        assert result == expected


class TestVolumeConfirmed:
    def test_above_threshold(self) -> None:
        assert is_volume_confirmed(
            Decimal("1500"), Decimal("1000"), Decimal("1.5")
        )

    def test_at_threshold(self) -> None:
        assert is_volume_confirmed(
            Decimal("1500"), Decimal("1000"), Decimal("1.5")
        )

    def test_below_threshold(self) -> None:
        assert not is_volume_confirmed(
            Decimal("1400"), Decimal("1000"), Decimal("1.5")
        )

    def test_zero_avg_returns_false(self) -> None:
        assert not is_volume_confirmed(
            Decimal("1000"), Decimal("0"), Decimal("1.5")
        )
