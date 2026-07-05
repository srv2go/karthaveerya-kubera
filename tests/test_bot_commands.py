import json
from datetime import UTC, datetime, timedelta

import pytest

from alphatrader import db
from alphatrader.bot import commands
from alphatrader.bot.telegram_app import gate
from alphatrader.data.market import MarketDataService, SymbolInfo
from alphatrader.models import Candle
from alphatrader.signals import lifecycle


class FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class FakeMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.replies: list[str] = []
        self.reply_markups: list[object] = []

    async def reply_text(self, text: str, reply_markup=None) -> None:
        self.replies.append(text)
        self.reply_markups.append(reply_markup)


class FakeUpdate:
    def __init__(self, chat_id):
        self.effective_chat = FakeChat(chat_id)
        self.message = FakeMessage()
        self.callback_query = None


class FakeCallbackQuery:
    def __init__(self, data: str, message_text: str = "card text"):
        self.data = data
        self.message = FakeMessage(text=message_text)
        self.answered = False
        self.edited_text: str | None = None

    async def answer(self) -> None:
        self.answered = True

    async def edit_message_text(self, text: str) -> None:
        self.edited_text = text


class FakeCallbackUpdate:
    def __init__(self, chat_id, data: str, message_text: str = "card text"):
        self.effective_chat = FakeChat(chat_id)
        self.callback_query = FakeCallbackQuery(data, message_text)
        self.message = None


class FakeContext:
    def __init__(self, bot_data: dict, args: list[str] | None = None):
        self.bot_data = bot_data
        self.args = args or []


ALLOWED_CHAT_ID = "12345"


@pytest.fixture
def seeded_db(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


def _insert_signal(conn, symbol="AAPL") -> int:
    cur = conn.execute(
        "INSERT INTO signals (symbol, action, entry, stop_loss, take_profit, units, "
        "risk_gbp, amount_gbp, risk_reward, confidence, rationale, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (symbol, "buy", 100.0, 98.0, 106.0, 2.5, 5.0, 250.0, 3.0, 0.7, "test",
         datetime.now(UTC).isoformat()),
    )
    return cur.lastrowid


def _context(db_path, initial_bankroll=1000.0, args=None, **extra):
    data = {
        "db_path": str(db_path),
        "initial_bankroll": initial_bankroll,
        "allowed_chat_id": ALLOWED_CHAT_ID,
    }
    data.update(extra)
    return FakeContext(data, args=args)


async def test_ping_replies_pong(seeded_db):
    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.ping(update, _context(seeded_db))
    assert update.message.replies == ["pong"]


async def test_gate_ignores_unknown_chat(seeded_db):
    update = FakeUpdate("99999")  # not the allowed chat
    gated_ping = gate(commands.ping)
    await gated_ping(update, _context(seeded_db))
    assert update.message.replies == []


async def test_gate_allows_configured_chat(seeded_db):
    update = FakeUpdate(ALLOWED_CHAT_ID)
    gated_ping = gate(commands.ping)
    await gated_ping(update, _context(seeded_db))
    assert update.message.replies == ["pong"]


async def test_help_replies_with_help_text(seeded_db):
    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.help_cmd(update, _context(seeded_db))
    assert "/scan" in update.message.replies[0]
    assert "/halt" in update.message.replies[0]


async def test_portfolio_empty(seeded_db):
    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.portfolio(update, _context(seeded_db))
    assert update.message.replies == ["No open positions."]


async def test_portfolio_lists_open_positions(seeded_db):
    with db.get_connection(seeded_db) as conn:
        signal_id = _insert_signal(conn)
        conn.execute(
            "INSERT INTO positions (signal_id, symbol, action, units, entry_price, "
            "stop_loss, take_profit, risk_gbp, opened_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (signal_id, "AAPL", "buy", 2.5, 100.0, 98.0, 106.0, 5.0,
             datetime.now(UTC).isoformat()),
        )
        conn.commit()

    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.portfolio(update, _context(seeded_db))
    assert len(update.message.replies) == 1
    assert "AAPL BUY 2.5000 units @ 100.00" in update.message.replies[0]


async def test_pnl_reports_realized_and_balance(seeded_db):
    with db.get_connection(seeded_db) as conn:
        signal_id = _insert_signal(conn)
        conn.execute(
            "INSERT INTO positions (signal_id, symbol, action, units, entry_price, "
            "stop_loss, take_profit, risk_gbp, opened_at, closed_at, close_price, "
            "realized_pnl_gbp) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                signal_id, "AAPL", "buy", 1.0, 100.0, 98.0, 106.0, 2.0,
                datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), 106.0, 6.0,
            ),
        )
        conn.commit()

    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.pnl(update, _context(seeded_db, initial_bankroll=1000.0))
    reply = update.message.replies[0]
    assert "Realized P&L: £6.00" in reply
    assert "Balance: £1006.00" in reply
    assert "Open positions: 0" in reply


async def test_signals_cmd_empty(seeded_db):
    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.signals_cmd(update, _context(seeded_db))
    assert update.message.replies == ["No pending signals."]


async def test_signals_cmd_lists_pending(seeded_db):
    with db.get_connection(seeded_db) as conn:
        _insert_signal(conn)
        conn.commit()

    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.signals_cmd(update, _context(seeded_db))
    reply = update.message.replies[0]
    assert "#1 AAPL BUY @ 100.00" in reply


async def test_filled_marks_signal_filled(seeded_db):
    with db.get_connection(seeded_db) as conn:
        signal_id = _insert_signal(conn)
        conn.commit()

    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.filled(update, _context(seeded_db, args=[str(signal_id)]))
    assert update.message.replies == [f"Signal #{signal_id} marked filled."]

    with db.get_connection(seeded_db) as conn:
        assert lifecycle.get_signal(conn, signal_id)["status"] == "filled"


async def test_filled_missing_args_shows_usage(seeded_db):
    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.filled(update, _context(seeded_db, args=[]))
    assert "Usage" in update.message.replies[0]


async def test_filled_twice_shows_lifecycle_error(seeded_db):
    with db.get_connection(seeded_db) as conn:
        signal_id = _insert_signal(conn)
        conn.commit()

    update = FakeUpdate(ALLOWED_CHAT_ID)
    ctx = _context(seeded_db, args=[str(signal_id)])
    await commands.filled(update, ctx)
    await commands.filled(update, ctx)
    assert "Error:" in update.message.replies[1]


async def test_skipped_marks_signal_skipped(seeded_db):
    with db.get_connection(seeded_db) as conn:
        signal_id = _insert_signal(conn)
        conn.commit()

    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.skipped(update, _context(seeded_db, args=[str(signal_id)]))
    assert update.message.replies == [f"Signal #{signal_id} marked skipped."]


async def test_closed_with_explicit_price(seeded_db, risk_config):
    with db.get_connection(seeded_db) as conn:
        signal_id = _insert_signal(conn)
        conn.commit()

    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.filled(update, _context(seeded_db, args=[str(signal_id)]))
    await commands.closed(
        update,
        _context(seeded_db, args=[str(signal_id), "106.0", "tp"], risk_config=risk_config),
    )
    reply = update.message.replies[-1]
    assert f"Signal #{signal_id} closed @ 106.00." in reply
    assert "Realized P&L: £15.00" in reply  # 2.5 units * (106 - 100)


async def test_closed_with_sl_shortcut(seeded_db, risk_config):
    with db.get_connection(seeded_db) as conn:
        signal_id = _insert_signal(conn)
        conn.commit()

    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.filled(update, _context(seeded_db, args=[str(signal_id)]))
    await commands.closed(
        update, _context(seeded_db, args=[str(signal_id), "sl"], risk_config=risk_config)
    )
    reply = update.message.replies[-1]
    assert "closed @ 98.00" in reply
    assert "Realized P&L: £-5.00" in reply  # 2.5 units * (98 - 100)


async def test_halt_and_resume_commands(seeded_db, risk_config):
    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.halt_cmd(update, _context(seeded_db, args=["testing"]))
    assert "Halted. Reason: testing" in update.message.replies[-1]

    await commands.resume_cmd(update, _context(seeded_db, risk_config=risk_config))
    assert "Agent state: ACTIVE" in update.message.replies[-1]


async def test_report_command(seeded_db):
    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.report(update, _context(seeded_db, initial_bankroll=1000.0))
    reply = update.message.replies[-1]
    assert "Balance: £1000.00" in reply
    assert "Agent state: ACTIVE" in reply
    assert "pending: 0" in reply


def _candles(symbol: str, closes: list[float]) -> list[Candle]:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(
            symbol=symbol,
            timestamp=base + timedelta(days=i),
            open=c - 0.5,
            high=c + 1.0,
            low=c - 1.0,
            close=c,
            volume=1_000_000,
        )
        for i, c in enumerate(closes)
    ]


class _FakeSource:
    def __init__(self, candles_by_symbol):
        self._candles = candles_by_symbol

    def get_quote(self, symbol):
        raise NotImplementedError

    def get_candles(self, symbol, limit=60):
        return self._candles[symbol]


class _ScriptedProvider:
    def __init__(self, responses: dict[str, str]):
        self._responses = responses

    def complete(self, system: str, user: str) -> str:
        for symbol, response in self._responses.items():
            if f"Symbol: {symbol}" in user:
                return response
        raise AssertionError(f"no scripted response for prompt: {user[:80]!r}")


def _proposal_json(symbol, action, entry, stop_loss, take_profit, confidence):
    return json.dumps(
        {
            "symbol": symbol, "action": action, "entry": entry, "stop_loss": stop_loss,
            "take_profit": take_profit, "confidence": confidence, "rationale": "test",
        }
    )


async def test_scan_command_persists_cards_and_replies_with_buttons(seeded_db, risk_config):
    candles = {"AAPL": _candles("AAPL", [float(90 + i) for i in range(60)])}
    symbols = {
        "AAPL": SymbolInfo(
            data_symbol="AAPL", etoro_name="AAPL", instrument_class="stock",
            typical_spread_bps=3.0,
        )
    }
    source = _FakeSource(candles)
    market = MarketDataService(symbols=symbols, stock_source=source, crypto_source=source)
    provider = _ScriptedProvider({"AAPL": _proposal_json("AAPL", "buy", 100.0, 99.0, 106.0, 0.8)})

    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.scan(
        update,
        _context(
            seeded_db, risk_config=risk_config, market=market, llm_provider=provider,
            symbols=["AAPL"],
        ),
    )

    assert len(update.message.replies) == 1
    assert "SIGNAL #1" in update.message.replies[0]
    assert update.message.reply_markups[0] is not None

    with db.get_connection(seeded_db) as conn:
        assert lifecycle.get_signal(conn, 1)["status"] == "pending"


async def test_scan_command_when_halted_does_not_scan(seeded_db, risk_config):
    with db.get_connection(seeded_db) as conn:
        lifecycle.halt(conn, reason="test")

    update = FakeUpdate(ALLOWED_CHAT_ID)
    await commands.scan(
        update,
        _context(
            seeded_db, risk_config=risk_config, market=None, llm_provider=None, symbols=["AAPL"]
        ),
    )
    assert "not scanning" in update.message.replies[0]


async def test_button_callback_filled_confirms_and_edits_message(seeded_db):
    with db.get_connection(seeded_db) as conn:
        signal_id = _insert_signal(conn)
        conn.commit()

    update = FakeCallbackUpdate(ALLOWED_CHAT_ID, f"filled:{signal_id}")
    await commands.button_callback(update, _context(seeded_db))

    assert update.callback_query.answered
    assert "Marked filled" in update.callback_query.edited_text
    with db.get_connection(seeded_db) as conn:
        assert lifecycle.get_signal(conn, signal_id)["status"] == "filled"


async def test_button_callback_skip_confirms_and_edits_message(seeded_db):
    with db.get_connection(seeded_db) as conn:
        signal_id = _insert_signal(conn)
        conn.commit()

    update = FakeCallbackUpdate(ALLOWED_CHAT_ID, f"skip:{signal_id}")
    await commands.button_callback(update, _context(seeded_db))

    assert "Skipped" in update.callback_query.edited_text
    with db.get_connection(seeded_db) as conn:
        assert lifecycle.get_signal(conn, signal_id)["status"] == "skipped"
