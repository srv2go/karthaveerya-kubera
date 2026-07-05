"""Structured logging setup (console + rotating JSON file) via loguru.

Never log secrets: callers should pass `settings.redacted()` rather than
the raw Settings object when logging configuration.
"""
from __future__ import annotations

import sys

from loguru import logger


def configure_logging(log_dir: str = "logs") -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO", backtrace=False, diagnose=False)
    logger.add(
        f"{log_dir}/alphatrader.jsonl",
        level="DEBUG",
        rotation="10 MB",
        retention="14 days",
        serialize=True,
        backtrace=False,
        diagnose=False,
    )
