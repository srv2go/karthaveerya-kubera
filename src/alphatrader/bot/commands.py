"""Telegram command handlers.

Phase 3 scope: read-only commands (/ping, /portfolio, /pnl). Phase 5 adds the
HITL loop: /scan proposes signal cards with inline buttons; /filled, /skipped,
/closed and the button callback confirm what the human actually did on eToro;
/halt, /resume, /report, /help round out manual control and visibility.

Handlers never call any broker/execution API — there is none. Every state
change goes through `signals/lifecycle.py`, which is the single write path to
the ledger and to agent_state.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from alphatrader.agent import pipeline
from alphatrader.db import get_connection
from alphatrader.models import SignalStatus
from alphatrader.signals import lifecycle
from alphatrader.signals.cards import render

HELP_TEXT = (
    "/scan - scan the watchlist for new signals\n"
    "/signals - list pending signals\n"
    "/filled <id> [price] - confirm you placed the trade\n"
    "/skipped <id> - confirm you skipped a signal\n"
    "/closed <id> <price|sl|tp> [reason] - confirm you closed a position\n"
    "/portfolio - list open positions\n"
    "/pnl - realized P&L and balance\n"
    "/halt [reason] - stop issuing new signals immediately\n"
    "/resume - re-evaluate breakers and resume if clear\n"
    "/report - summary of balance, agent state, and signal counts\n"
    "/ping - health check"
)


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT)


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


def card_keyboard(signal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("I placed it", callback_data=f"filled:{signal_id}"),
                InlineKeyboardButton("Skip", callback_data=f"skip:{signal_id}"),
            ]
        ]
    )


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path = context.bot_data["db_path"]
    risk_cfg = context.bot_data["risk_config"]
    market = context.bot_data["market"]
    provider = context.bot_data["llm_provider"]
    symbols = context.bot_data["symbols"]
    initial_bankroll = context.bot_data["initial_bankroll"]

    with get_connection(db_path) as conn:
        agent_state = lifecycle.get_agent_state(conn)
        if agent_state.value != "ACTIVE":
            await update.message.reply_text(
                f"Agent is {agent_state.value}; not scanning. Use /resume once ready."
            )
            return
        balance = lifecycle.current_balance_gbp(conn, initial_bankroll)
        open_count = conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE closed_at IS NULL"
        ).fetchone()["n"]

    cards = pipeline.scan(
        symbols, market, provider, risk_cfg, balance, open_count, agent_state
    )
    if not cards:
        await update.message.reply_text("No signals passed the risk engine this scan.")
        return

    with get_connection(db_path) as conn:
        persisted = lifecycle.persist_cards(conn, cards)

    for card in persisted:
        await update.message.reply_text(
            render(card), reply_markup=card_keyboard(card.signal_id)
        )


async def signals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path = context.bot_data["db_path"]
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, symbol, action, entry, expires_at FROM signals "
            "WHERE status = ? ORDER BY created_at",
            (SignalStatus.PENDING.value,),
        ).fetchall()

    if not rows:
        await update.message.reply_text("No pending signals.")
        return

    lines = ["Pending signals:"]
    for r in rows:
        lines.append(
            f"#{r['id']} {r['symbol']} {r['action'].upper()} @ {r['entry']:.2f} "
            f"(expires {r['expires_at']})"
        )
    await update.message.reply_text("\n".join(lines))


def _parse_signal_id(args: list[str]) -> int | None:
    if not args:
        return None
    try:
        return int(args[0])
    except ValueError:
        return None


async def filled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    signal_id = _parse_signal_id(context.args)
    if signal_id is None:
        await update.message.reply_text("Usage: /filled <signal_id> [fill_price]")
        return

    fill_price = None
    if len(context.args) > 1:
        try:
            fill_price = float(context.args[1])
        except ValueError:
            await update.message.reply_text("Usage: /filled <signal_id> [fill_price]")
            return

    db_path = context.bot_data["db_path"]
    with get_connection(db_path) as conn:
        try:
            lifecycle.mark_filled(conn, signal_id, fill_price)
        except lifecycle.LifecycleError as exc:
            await update.message.reply_text(f"Error: {exc}")
            return
    await update.message.reply_text(f"Signal #{signal_id} marked filled.")


async def skipped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    signal_id = _parse_signal_id(context.args)
    if signal_id is None:
        await update.message.reply_text("Usage: /skipped <signal_id>")
        return

    db_path = context.bot_data["db_path"]
    with get_connection(db_path) as conn:
        try:
            lifecycle.mark_skipped(conn, signal_id)
        except lifecycle.LifecycleError as exc:
            await update.message.reply_text(f"Error: {exc}")
            return
    await update.message.reply_text(f"Signal #{signal_id} marked skipped.")


async def closed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    signal_id = _parse_signal_id(args)
    if signal_id is None or len(args) < 2:
        await update.message.reply_text("Usage: /closed <signal_id> <close_price|sl|tp> [reason]")
        return

    price_arg = args[1]
    reason = " ".join(args[2:]) if len(args) > 2 else price_arg

    db_path = context.bot_data["db_path"]
    risk_cfg = context.bot_data["risk_config"]
    initial_bankroll = context.bot_data["initial_bankroll"]

    with get_connection(db_path) as conn:
        if price_arg.lower() in ("sl", "tp"):
            try:
                signal = lifecycle.get_signal(conn, signal_id)
            except lifecycle.LifecycleError as exc:
                await update.message.reply_text(f"Error: {exc}")
                return
            close_price = (
                signal["stop_loss"] if price_arg.lower() == "sl" else signal["take_profit"]
            )
        else:
            try:
                close_price = float(price_arg)
            except ValueError:
                await update.message.reply_text(
                    "Usage: /closed <signal_id> <close_price|sl|tp> [reason]"
                )
                return

        try:
            realized_pnl = lifecycle.close_position(conn, signal_id, close_price, reason=reason)
        except lifecycle.LifecycleError as exc:
            await update.message.reply_text(f"Error: {exc}")
            return

        result = lifecycle.refresh_agent_state(conn, risk_cfg, initial_bankroll)

    await update.message.reply_text(
        f"Signal #{signal_id} closed @ {close_price:.2f}. "
        f"Realized P&L: £{realized_pnl:.2f}. Agent state: {result.state.value}"
    )


async def halt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reason = " ".join(context.args) if context.args else "manual halt"
    db_path = context.bot_data["db_path"]
    with get_connection(db_path) as conn:
        lifecycle.halt(conn, reason=reason)
    await update.message.reply_text(f"Halted. Reason: {reason}")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path = context.bot_data["db_path"]
    risk_cfg = context.bot_data["risk_config"]
    initial_bankroll = context.bot_data["initial_bankroll"]
    with get_connection(db_path) as conn:
        result = lifecycle.resume(conn, risk_cfg, initial_bankroll)

    text = f"Agent state: {result.state.value}"
    if result.reason:
        text += f" ({result.reason})"
    await update.message.reply_text(text)


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path = context.bot_data["db_path"]
    initial_bankroll = context.bot_data["initial_bankroll"]
    with get_connection(db_path) as conn:
        balance = lifecycle.current_balance_gbp(conn, initial_bankroll)
        agent_state = lifecycle.get_agent_state(conn)
        counts = {}
        for status in SignalStatus:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM signals WHERE status = ?", (status.value,)
            ).fetchone()
            counts[status.value] = row["n"]
        open_row = conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE closed_at IS NULL"
        ).fetchone()

    await update.message.reply_text(
        f"Balance: £{balance:.2f}\n"
        f"Agent state: {agent_state.value}\n"
        f"Open positions: {open_row['n']}\n"
        f"Signals — pending: {counts['pending']}, filled: {counts['filled']}, "
        f"skipped: {counts['skipped']}, expired: {counts['expired']}, "
        f"closed: {counts['closed']}"
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the [I placed it] / [Skip] inline buttons on signal cards."""
    query = update.callback_query
    await query.answer()

    action, _, signal_id_str = query.data.partition(":")
    try:
        signal_id = int(signal_id_str)
    except ValueError:
        return

    db_path = context.bot_data["db_path"]
    with get_connection(db_path) as conn:
        try:
            if action == "filled":
                lifecycle.mark_filled(conn, signal_id)
                suffix = "\n\n✅ Marked filled."
            elif action == "skip":
                lifecycle.mark_skipped(conn, signal_id)
                suffix = "\n\n⏭ Skipped."
            else:
                return
        except lifecycle.LifecycleError as exc:
            suffix = f"\n\n⚠ Error: {exc}"

    await query.edit_message_text(query.message.text + suffix)
