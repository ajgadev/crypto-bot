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

    # Mean-reversion strategy
    mean_reversion_enabled: bool = True
    mean_reversion_rsi_max: Decimal = Decimal("50")
    mean_reversion_pct_drop: Decimal = Decimal("-0.01")
    mean_reversion_rsi_exit: Decimal = Decimal("70")
    mean_reversion_trend_filter: bool = True
    mean_reversion_trend_ema: int = 300

    # Mean-reversion regime-adaptive (bear market overrides)
    mean_reversion_regime_adaptive: bool = False
    mean_reversion_regime_ema: int = 200
    mean_reversion_regime_reference: str = "BTCUSDC"
    mean_reversion_bear_rsi_max: Decimal = Decimal("50")
    mean_reversion_bear_pct_drop: Decimal = Decimal("-0.01")
    mean_reversion_bear_tp_pct: Decimal = Decimal("0.03")
    mean_reversion_bear_sl_pct: Decimal = Decimal("0.03")

    # Risk management
    max_open_trades: int = 2
    reserve_pct: Decimal = Decimal("0.10")
    risk_pct: Decimal = Decimal("0.02")
    take_profit_pct: Decimal = Decimal("0.04")
    stop_loss_pct: Decimal = Decimal("0.05")

    # Trend-follow strategy
    trend_follow_enabled: bool = True
    trend_follow_max_trades: int = 2
    trend_follow_trailing_stop_pct: Decimal = Decimal("0.15")
    trend_follow_rsi_min: Decimal = Decimal("50")
    trend_follow_rsi_max: Decimal = Decimal("70")
    trend_follow_volume_multiplier: Decimal = Decimal("1.2")
    trend_follow_volume_period: int = 20
    trend_follow_crossover_window: int = 3
    trend_follow_ema_short: int = 20
    trend_follow_ema_long: int = 50

    # Momentum strategy (TF entries + fixed TP/SL exits)
    momentum_enabled: bool = False
    momentum_max_trades: int = 2
    momentum_take_profit_pct: Decimal = Decimal("0.025")
    momentum_stop_loss_pct: Decimal = Decimal("0.022")
    momentum_rsi_min: Decimal = Decimal("50")
    momentum_rsi_max: Decimal = Decimal("70")
    momentum_volume_multiplier: Decimal = Decimal("1.2")
    momentum_volume_period: int = 20
    momentum_crossover_window: int = 3
    momentum_ema_short: int = 20
    momentum_ema_long: int = 50

    # Defensive mode (bear market protection)
    defensive_mode_enabled: bool = False
    defensive_mode_ema: int = 200
    defensive_mode_reference: str = "BTCUSDC"

    # Logging
    log_level: str = "INFO"

    # Telegram notifications
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_report_interval_hours: int = 8

    # AI daily report (requires anthropic SDK)
    anthropic_api_key: str = ""
    ai_daily_report_enabled: bool = False
    ai_daily_report_hour: int = 20  # UTC hour to send

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

    @property
    def momentum_tp_multiplier(self) -> Decimal:
        return Decimal("1") + self.momentum_take_profit_pct

    @property
    def momentum_sl_multiplier(self) -> Decimal:
        return Decimal("1") - self.momentum_stop_loss_pct

    @property
    def momentum_sl_limit_multiplier(self) -> Decimal:
        """Slightly below SL trigger for fill assurance."""
        return Decimal("1") - self.momentum_stop_loss_pct - Decimal("0.005")

    @property
    def tf_trailing_stop_multiplier(self) -> Decimal:
        """Trailing stop: sell when price drops this fraction from peak."""
        return Decimal("1") - self.trend_follow_trailing_stop_pct

    def with_bear_mr_params(self) -> Settings:
        """Return a copy with bear market MR overrides applied."""
        return self.model_copy(update={
            "mean_reversion_rsi_max": self.mean_reversion_bear_rsi_max,
            "mean_reversion_pct_drop": self.mean_reversion_bear_pct_drop,
            "take_profit_pct": self.mean_reversion_bear_tp_pct,
            "stop_loss_pct": self.mean_reversion_bear_sl_pct,
        })
