"""Telegram command handlers.

Phase 3 scope: read-only commands (/ping, /portfolio, /pnl) against the
SQLite DB. Handlers never call any broker/execution API — there is none.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from alphatrader.db import get_connection


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong")


async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path = context.bot_data["db_path"]
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT symbol, action, units, entry_price, stop_loss, take_profit, risk_gbp "
            "FROM positions WHERE closed_at IS NULL ORDER BY opened_at"
        ).fetchall()

    if not rows:
        await update.message.reply_text("No open positions.")
        return

    lines = ["Open positions:"]
    for r in rows:
        lines.append(
            f"{r['symbol']} {r['action'].upper()} {r['units']:.4f} units @ {r['entry_price']:.2f} "
            f"(SL {r['stop_loss']:.2f} / TP {r['take_profit']:.2f}, risk £{r['risk_gbp']:.2f})"
        )
    await update.message.reply_text("\n".join(lines))


async def pnl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path = context.bot_data["db_path"]
    initial_bankroll = context.bot_data["initial_bankroll"]
    with get_connection(db_path) as conn:
        realized_row = conn.execute(
            "SELECT COALESCE(SUM(realized_pnl_gbp), 0) AS realized "
            "FROM positions WHERE closed_at IS NOT NULL"
        ).fetchone()
        open_row = conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE closed_at IS NULL"
        ).fetchone()

    realized = realized_row["realized"]
    balance = initial_bankroll + realized
    await update.message.reply_text(
        f"Realized P&L: £{realized:.2f}\n"
        f"Balance: £{balance:.2f}\n"
        f"Open positions: {open_row['n']}"
    )
