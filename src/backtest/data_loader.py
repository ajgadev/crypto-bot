"""Download and load kline data for backtesting."""

from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime
from decimal import Decimal

import httpx

from src.binance.types import Kline

BINANCE_BASE = "https://api.binance.com"


def _ts_ms(dt_str: str) -> int:
    """Convert YYYY-MM-DD string to millisecond timestamp."""
    dt = datetime.strptime(dt_str, "%Y-%m-%d")
    return int(dt.timestamp() * 1000)


async def download_klines(
    symbol: str,
    start: str,
    end: str,
    interval: str = "1h",
    output: str | None = None,
) -> list[Kline]:
    """Download klines from Binance public API and optionally save to CSV."""
    start_ms = _ts_ms(start)
    end_ms = _ts_ms(end)
    all_klines: list[Kline] = []

    async with httpx.AsyncClient(base_url=BINANCE_BASE, timeout=30.0) as client:
        current = start_ms
        while current < end_ms:
            resp = await client.get(
                "/api/v3/klines",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": current,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break

            for k in data:
                all_klines.append(
                    Kline(
                        open_time=int(k[0]),
                        open=Decimal(str(k[1])),
                        high=Decimal(str(k[2])),
                        low=Decimal(str(k[3])),
                        close=Decimal(str(k[4])),
                        volume=Decimal(str(k[5])),
                        close_time=int(k[6]),
                    )
                )
            # Move past last candle
            current = int(data[-1][6]) + 1

    if output:
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        with open(output, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["open_time", "open", "high", "low", "close", "volume"])
            for k in all_klines:
                writer.writerow([k.open_time, k.open, k.high, k.low, k.close, k.volume])

    return all_klines


def load_csv(path: str) -> list[Kline]:
    """Load klines from a previously downloaded CSV."""
    klines: list[Kline] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            klines.append(
                Kline(
                    open_time=int(row["open_time"]),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=Decimal(row["volume"]),
                    close_time=0,
                )
            )
    return klines


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description="Download Binance klines")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    asyncio.run(download_klines(args.symbol, args.start, args.end, output=args.output))
    print(f"Downloaded klines to {args.output}")
