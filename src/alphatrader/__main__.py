"""Entry point: config -> db -> bot -> scheduler.

Run `python -m alphatrader --check` to validate configuration and the
database without starting the bot or any scheduled jobs.
"""
from __future__ import annotations

import argparse
import sys

from loguru import logger

from alphatrader import db
from alphatrader.logging_setup import configure_logging
from alphatrader.risk.engine import RiskConfigError, load_risk_config
from alphatrader.settings import get_settings


def check(settings=None) -> int:
    """Validate config against compiled-in ceilings and initialize the DB.

    Returns process exit code (0 = ok, 1 = failure).
    """
    settings = settings or get_settings()
    logger.info("Validating configuration: {}", settings.redacted())

    try:
        risk_cfg = load_risk_config(str(settings.risk_config_path))
    except (RiskConfigError, FileNotFoundError, KeyError) as exc:
        logger.error("Config validation failed: {}", exc)
        return 1

    if not settings.symbols_config_path.exists():
        logger.error("Symbols config not found at {}", settings.symbols_config_path)
        return 1

    db.init_db(settings.db_path)
    logger.info(
        "Config OK: bankroll={} {}, max_risk_per_trade={}%, min_rr={}, max_positions={}",
        risk_cfg.initial_bankroll,
        risk_cfg.currency,
        risk_cfg.max_risk_per_trade_percent,
        risk_cfg.min_risk_reward_ratio,
        risk_cfg.max_concurrent_positions,
    )
    logger.info("Database initialized at {}", settings.db_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alphatrader")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate config/DB and exit without starting the bot or scheduler.",
    )
    args = parser.parse_args(argv)

    configure_logging()
    settings = get_settings()

    if args.check:
        return check(settings)

    logger.error(
        "Running the live bot/scheduler is not wired up in this build step; "
        "use --check to validate configuration."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
