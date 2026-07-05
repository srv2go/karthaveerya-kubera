from datetime import UTC, datetime

import pytest

from alphatrader import db
from alphatrader.bot import commands
from alphatrader.bot.telegram_app import gate


class FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class FakeMessage:
    def __init__(self):
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, chat_id):
        self.effective_chat = FakeChat(chat_id)
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, bot_data: dict):
        self.bot_data = bot_data


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


def _context(db_path, initial_bankroll=1000.0):
    return FakeContext(
        {
            "db_path": str(db_path),
            "initial_bankroll": initial_bankroll,
            "allowed_chat_id": ALLOWED_CHAT_ID,
        }
    )


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
