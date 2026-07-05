"""Telegram application wiring and chat-ID gating.

Every command is wrapped by `_gate`, which silently ignores updates from any
chat other than the configured `TELEGRAM_CHAT_ID`. This is a single-user bot
by design.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from alphatrader.bot import commands

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def chat_allowed(update: Update, allowed_chat_id: str) -> bool:
    chat = update.effective_chat
    return chat is not None and str(chat.id) == str(allowed_chat_id)


def gate(handler: Handler) -> Handler:
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        allowed_chat_id = context.bot_data.get("allowed_chat_id")
        if not chat_allowed(update, allowed_chat_id):
            return
        await handler(update, context)

    return wrapper


def build_application(
    token: str, allowed_chat_id: str, db_path: str, initial_bankroll: float
) -> Application:
    application = Application.builder().token(token).build()
    application.bot_data["db_path"] = db_path
    application.bot_data["initial_bankroll"] = initial_bankroll
    application.bot_data["allowed_chat_id"] = allowed_chat_id

    application.add_handler(CommandHandler("ping", gate(commands.ping)))
    application.add_handler(CommandHandler("portfolio", gate(commands.portfolio)))
    application.add_handler(CommandHandler("pnl", gate(commands.pnl)))
    return application
