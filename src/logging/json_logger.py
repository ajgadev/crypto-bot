"""JSON formatter and rotating file handler for structured logging."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any


RUN_ID: str = uuid.uuid4().hex[:12]


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": RUN_ID,
            "level": record.levelname,
            "event": record.getMessage(),
        }
        # Merge extra fields attached via logger.info("msg", extra={...})
        for key in (
            "symbol", "indicators", "bias", "decision",
            "budgets", "order_params", "result", "error",
        ):
            val = getattr(record, key, None)
            if val is not None:
                log_obj[key] = val
        return json.dumps(log_obj, default=str)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure root logger with JSON formatter to stdout + rotating file."""
    logger = logging.getLogger("crypto_bot")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if logger.handlers:
        return logger

    formatter = JsonFormatter()

    # Stdout handler
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # Rotating file handler
    log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "bot.log"),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
