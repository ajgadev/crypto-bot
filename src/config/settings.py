"""Application settings loaded from environment variables."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunMode(StrEnum):
    """Execution mode for the bot."""

    LIVE = "live"
    DRY_RUN = "dry_run"
    BACKTEST = "backtest"


class Settings(BaseSettings):
    """Bot configuration validated via pydantic-settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Binance credentials
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True

    # Execution
    run_mode: RunMode = RunMode.DRY_RUN
    quote_asset: str = "USDC"
    symbols: str = "BTCUSDC,ETHUSDC,BNBUSDC,SOLUSDC"

    # Risk management
    max_open_trades: int = 2
    reserve_pct: Decimal = Decimal("0.20")
    risk_pct: Decimal = Decimal("0.02")
    take_profit_pct: Decimal = Decimal("0.04")
    stop_loss_pct: Decimal = Decimal("0.03")

    # Logging
    log_level: str = "INFO"

    @property
    def symbols_list(self) -> list[str]:
        """Parse comma-separated symbols string into list."""
        return [s.strip() for s in self.symbols.split(",") if s.strip()]

    @property
    def base_url(self) -> str:
        if self.binance_testnet:
            return "https://testnet.binance.vision"
        return "https://api.binance.com"

    @property
    def tp_multiplier(self) -> Decimal:
        return Decimal("1") + self.take_profit_pct

    @property
    def sl_multiplier(self) -> Decimal:
        return Decimal("1") - self.stop_loss_pct

    @property
    def sl_limit_multiplier(self) -> Decimal:
        """Slightly below SL trigger for fill assurance."""
        return Decimal("1") - self.stop_loss_pct - Decimal("0.005")
