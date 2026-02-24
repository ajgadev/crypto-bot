"""Test configuration - prevent .env file from interfering with tests."""

import os

# Prevent pydantic-settings from loading the real .env file during tests
os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")
