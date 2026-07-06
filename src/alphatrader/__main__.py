"""Entry point: config -> db -> bot -> scheduler.

Run `python -m alphatrader --check` to validate configuration and the
database without starting the bot or any scheduled jobs.

Run `python -m alphatrader` to start the Telegram bot and the scheduled jobs
(daily scan, 15-min mark-to-market, daily report, weekly review). There is no
code path here — or anywhere in this codebase — that places, modifies, or
cancels an order; every job below only reads market data and writes to the
local ledger/signal tables that `signals/lifecycle.py` owns.
"""
from __future__ import annotations

import argparse
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from telegram.ext import Application

from alphatrader import db
from alphatrader.agent import pipeline
from alphatrader.bot import commands, telegram_app
from alphatrader.data.market import load_symbols_config
from alphatrader.db import get_connection
from alphatrader.factories import build_llm_provider, build_market
from alphatrader.logging_setup import configure_logging
from alphatrader.reporting.daily import build_daily_report, build_weekly_review
from alphatrader.risk.engine import RiskConfig, RiskConfigError, load_risk_config
from alphatrader.settings import Settings, get_settings
from alphatrader.signals import lifecycle
from alphatrader.signals.cards import render


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


async def _scheduled_scan(application: Application) -> None:
    """07:30 UTC daily scan: propose, risk-evaluate, persist, and post cards."""
    bot_data = application.bot_data
    db_path = bot_data["db_path"]
    risk_cfg = bot_data["risk_config"]
    market = bot_data["market"]
    provider = bot_data["llm_provider"]
    symbols = bot_data["symbols"]
    initial_bankroll = bot_data["initial_bankroll"]
    chat_id = bot_data["allowed_chat_id"]

    with get_connection(db_path) as conn:
        agent_state = lifecycle.get_agent_state(conn)
        if agent_state.value != "ACTIVE":
            logger.info("Scheduled scan skipped: agent state is {}", agent_state.value)
            return
        balance = lifecycle.current_balance_gbp(conn, initial_bankroll)
        open_count = conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE closed_at IS NULL"
        ).fetchone()["n"]

    cards = pipeline.scan(symbols, market, provider, risk_cfg, balance, open_count, agent_state)
    if not cards:
        await application.bot.send_message(chat_id, "No signals passed the risk engine this scan.")
        return

    with get_connection(db_path) as conn:
        persisted = lifecycle.persist_cards(conn, cards)

    for card in persisted:
        await application.bot.send_message(
            chat_id, render(card), reply_markup=commands.card_keyboard(card.signal_id)
        )


async def _scheduled_mark_to_market(application: Application) -> None:
    """15-min job: feed live quotes into breaker checks and expire due signals.

    Never touches eToro or any broker — if the state changes to a halted
    state, the alert is "consider closing / review", never an automated action.
    """
    bot_data = application.bot_data
    db_path = bot_data["db_path"]
    risk_cfg = bot_data["risk_config"]
    market = bot_data["market"]
    initial_bankroll = bot_data["initial_bankroll"]
    chat_id = bot_data["allowed_chat_id"]

    with get_connection(db_path) as conn:
        previous_state = lifecycle.get_agent_state(conn)
        lifecycle.expire_due_signals(conn)
        result = lifecycle.mark_to_market(conn, risk_cfg, initial_bankroll, market)

    if result.state.value != previous_state.value and result.state.value != "ACTIVE":
        await application.bot.send_message(
            chat_id,
            f"⚠ Agent state changed to {result.state.value} ({result.reason}).\n"
            "No positions are closed automatically — consider closing/reviewing "
            "your open positions on eToro.",
        )


async def _scheduled_daily_report(application: Application) -> None:
    """21:00 UTC daily report."""
    bot_data = application.bot_data
    db_path = bot_data["db_path"]
    risk_cfg = bot_data["risk_config"]
    market = bot_data["market"]
    initial_bankroll = bot_data["initial_bankroll"]
    chat_id = bot_data["allowed_chat_id"]

    with get_connection(db_path) as conn:
        text = build_daily_report(conn, risk_cfg, initial_bankroll, market=market)
    await application.bot.send_message(chat_id, text)


async def _scheduled_weekly_review(application: Application) -> None:
    """Sunday weekly review of skipped-vs-taken hypothetical outcomes."""
    bot_data = application.bot_data
    db_path = bot_data["db_path"]
    market = bot_data["market"]
    chat_id = bot_data["allowed_chat_id"]

    with get_connection(db_path) as conn:
        text = build_weekly_review(conn, market)
    await application.bot.send_message(chat_id, text)


def _start_scheduler(application: Application) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _scheduled_scan, CronTrigger(hour=7, minute=30), args=[application], id="daily_scan"
    )
    scheduler.add_job(
        _scheduled_mark_to_market,
        CronTrigger(minute="*/15"),
        args=[application],
        id="mark_to_market",
    )
    scheduler.add_job(
        _scheduled_daily_report,
        CronTrigger(hour=21, minute=0),
        args=[application],
        id="daily_report",
    )
    scheduler.add_job(
        _scheduled_weekly_review,
        CronTrigger(day_of_week="sun", hour=21, minute=5),
        args=[application],
        id="weekly_review",
    )
    scheduler.start()
    return scheduler


def run(settings: Settings) -> int:
    """Build and run the live Telegram bot with all scheduled jobs. Blocks
    until interrupted.
    """
    risk_cfg: RiskConfig = load_risk_config(str(settings.risk_config_path))
    db.init_db(settings.db_path)

    market = build_market(settings)
    provider = build_llm_provider(settings)
    symbols = list(load_symbols_config(str(settings.symbols_config_path)).keys())

    application = telegram_app.build_application(
        token=settings.telegram_bot_token,
        allowed_chat_id=settings.telegram_chat_id,
        db_path=str(settings.db_path),
        initial_bankroll=risk_cfg.initial_bankroll,
        risk_config=risk_cfg,
        market=market,
        llm_provider=provider,
        symbols=symbols,
    )

    async def _post_init(app: Application) -> None:
        app.bot_data["scheduler"] = _start_scheduler(app)
        logger.info("Scheduler started: daily scan 07:30 UTC, mark-to-market every 15 min, "
                     "daily report 21:00 UTC, weekly review Sunday 21:05 UTC")

    application.post_init = _post_init
    application.run_polling()
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

    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
