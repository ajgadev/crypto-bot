"""Binance REST API client with HMAC-SHA256 signing and retries."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from src.binance.types import Kline, OrderResponse, SymbolFilters, TickerPrice
from src.config.settings import Settings

logger = logging.getLogger("crypto_bot")

MAX_RETRIES = 3
RETRY_BACKOFF = [1.0, 2.0, 4.0]


class BinanceClient:
    """Async httpx-based Binance Spot API client."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.base_url
        self._api_key = settings.binance_api_key
        self._api_secret = settings.binance_api_secret
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> BinanceClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"X-MBX-APIKEY": self._api_key},
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._client:
            await self._client.aclose()

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add timestamp and HMAC-SHA256 signature to params."""
        params["timestamp"] = int(time.time() * 1000)
        query_string = urlencode(params)
        signature = hmac.new(
            self._api_secret.encode(),
            query_string.encode(),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        """Execute HTTP request with retry logic."""
        assert self._client is not None, "Client not initialized. Use async with."
        if params is None:
            params = {}
        if signed:
            params = self._sign(params)

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                if method == "GET":
                    resp = await self._client.get(path, params=params)
                else:
                    resp = await self._client.post(path, params=params)

                # Log weight usage
                weight = resp.headers.get("X-MBX-USED-WEIGHT-1M")
                if weight:
                    logger.debug("Binance weight used: %s", weight)

                if resp.status_code in (429, 418):
                    retry_after = int(resp.headers.get("Retry-After", RETRY_BACKOFF[attempt]))
                    logger.warning(
                        "Rate limited (HTTP %d), retrying after %ds",
                        resp.status_code,
                        retry_after,
                    )
                    await _async_sleep(retry_after)
                    continue

                if resp.status_code >= 500:
                    logger.warning(
                        "Server error (HTTP %d), retry %d/%d",
                        resp.status_code,
                        attempt + 1,
                        MAX_RETRIES,
                    )
                    await _async_sleep(RETRY_BACKOFF[attempt])
                    continue

                resp.raise_for_status()
                return resp.json()

            except httpx.HTTPError as exc:
                last_exc = exc
                # Strip signature and timestamp from error to avoid leaking secrets
                safe_msg = re.sub(
                    r"(&?)(signature|timestamp)=[^&'\")\s]+", "", str(exc)
                )
                logger.warning("HTTP error: %s, retry %d/%d", safe_msg, attempt + 1, MAX_RETRIES)
                await _async_sleep(RETRY_BACKOFF[attempt])

        raise RuntimeError(f"Binance API failed after {MAX_RETRIES} retries") from last_exc

    # ── Public API methods ──

    async def get_account(self) -> dict[str, Any]:
        """GET /api/v3/account - balances and account info."""
        return await self._request("GET", "/api/v3/account", signed=True)  # type: ignore[return-value]

    async def get_quote_balance(self, quote_asset: str = "USDC") -> Decimal:
        """Return free balance for the quote asset (USDC, USDT, etc.)."""
        account: dict[str, Any] = await self.get_account()
        for bal in account.get("balances", []):
            if bal["asset"] == quote_asset:
                return Decimal(str(bal["free"]))
        return Decimal("0")

    async def get_asset_balance(self, asset: str) -> Decimal:
        """Return free balance for a given asset."""
        account: dict[str, Any] = await self.get_account()
        for bal in account.get("balances", []):
            if bal["asset"] == asset:
                return Decimal(str(bal["free"]))
        return Decimal("0")

    async def get_exchange_info(self, symbol: str) -> SymbolFilters:
        """GET /api/v3/exchangeInfo for a single symbol, parse filters."""
        data: dict[str, Any] = await self._request(
            "GET", "/api/v3/exchangeInfo", params={"symbol": symbol}
        )
        sym_info = data["symbols"][0]
        filters_raw = {f["filterType"]: f for f in sym_info["filters"]}

        lot = filters_raw.get("LOT_SIZE", {})
        notional = filters_raw.get("NOTIONAL", filters_raw.get("MIN_NOTIONAL", {}))
        price = filters_raw.get("PRICE_FILTER", {})

        return SymbolFilters(
            symbol=symbol,
            min_notional=Decimal(str(notional.get("minNotional", "10"))),
            lot_step_size=Decimal(str(lot.get("stepSize", "0.00000100"))),
            lot_min_qty=Decimal(str(lot.get("minQty", "0.00000100"))),
            lot_max_qty=Decimal(str(lot.get("maxQty", "9999999"))),
            price_tick_size=Decimal(str(price.get("tickSize", "0.01"))),
        )

    async def get_klines(
        self, symbol: str, interval: str = "1h", limit: int = 50
    ) -> list[Kline]:
        """GET /api/v3/klines - candlestick data."""
        data: list[list[Any]] = await self._request(
            "GET",
            "/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        return [
            Kline(
                open_time=int(k[0]),
                open=Decimal(str(k[1])),
                high=Decimal(str(k[2])),
                low=Decimal(str(k[3])),
                close=Decimal(str(k[4])),
                volume=Decimal(str(k[5])),
                close_time=int(k[6]),
            )
            for k in data
        ]

    async def get_ticker_price(self, symbol: str) -> TickerPrice:
        """GET /api/v3/ticker/price."""
        data: dict[str, Any] = await self._request(
            "GET", "/api/v3/ticker/price", params={"symbol": symbol}
        )
        return TickerPrice(symbol=data["symbol"], price=Decimal(str(data["price"])))

    async def place_market_order(
        self, symbol: str, side: str, quantity: Decimal
    ) -> OrderResponse:
        """POST /api/v3/order - market order."""
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": str(quantity),
        }
        data: dict[str, Any] = await self._request("POST", "/api/v3/order", params=params, signed=True)
        return OrderResponse.model_validate(data)

    async def place_oco_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        stop_price: Decimal,
        stop_limit_price: Decimal,
    ) -> dict[str, Any]:
        """POST /api/v3/order/oco - one-cancels-other order."""
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "quantity": str(quantity),
            "price": str(price),
            "stopPrice": str(stop_price),
            "stopLimitPrice": str(stop_limit_price),
            "stopLimitTimeInForce": "GTC",
        }
        return await self._request("POST", "/api/v3/order/oco", params=params, signed=True)  # type: ignore[return-value]


async def _async_sleep(seconds: float) -> None:
    """Async-compatible sleep."""
    import asyncio

    await asyncio.sleep(seconds)
