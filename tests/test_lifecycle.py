from datetime import UTC, datetime, timedelta

import pytest

from alphatrader import db
from alphatrader.models import AgentStateName, Quote
from alphatrader.signals import lifecycle
from alphatrader.signals.cards import SignalCard


class _FakeMarket:
    def __init__(self, quotes: dict[str, float]):
        self._quotes = quotes

    def get_quote(self, symbol: str) -> Quote:
        return Quote(symbol=symbol, price=self._quotes[symbol], timestamp=datetime.now(UTC))


@pytest.fixture
def seeded_db(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


def _card(**overrides) -> SignalCard:
    defaults = dict(
        signal_id=999,  # placeholder; persist_cards() assigns the real id
        symbol="AAPL",
        action="buy",
        entry=100.0,
        stop_loss=99.0,
        take_profit=106.0,
        units=10.0,
        risk_gbp=10.0,
        amount_gbp=1000.0,
        risk_reward=6.0,
        spread_multiple=33.0,
        rationale="test rationale",
        expires_at=datetime.now(UTC) + timedelta(hours=4),
        initial_bankroll=1000.0,
        risk_pct=1.0,
        confidence=0.7,
        instrument_class="stock",
    )
    defaults.update(overrides)
    return SignalCard(**defaults)


def test_persist_cards_assigns_real_ids_and_pending_status(seeded_db):
    with db.get_connection(seeded_db) as conn:
        persisted = lifecycle.persist_cards(conn, [_card()])
        assert persisted[0].signal_id != 999

        row = lifecycle.get_signal(conn, persisted[0].signal_id)
        assert row["status"] == "pending"
        assert row["symbol"] == "AAPL"


def test_full_loop_scan_to_filled_to_closed_updates_ledger(seeded_db, risk_config):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card()])
        signal_id = card.signal_id

        lifecycle.mark_filled(conn, signal_id, fill_price=100.0)
        signal_row = lifecycle.get_signal(conn, signal_id)
        assert signal_row["status"] == "filled"
        position = conn.execute(
            "SELECT * FROM positions WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        assert position["closed_at"] is None
        assert position["entry_price"] == 100.0

        pnl = lifecycle.close_position(conn, signal_id, close_price=106.0, reason="tp")
        assert pnl == pytest.approx(60.0)  # 10 units * (106 - 100)

        signal_row = lifecycle.get_signal(conn, signal_id)
        assert signal_row["status"] == "closed"
        assert signal_row["close_reason"] == "tp"

        balance = lifecycle.current_balance_gbp(conn, risk_config.initial_bankroll)
        assert balance == pytest.approx(1060.0)

        result = lifecycle.refresh_agent_state(conn, risk_config, risk_config.initial_bankroll)
        assert result.state == AgentStateName.ACTIVE
        assert lifecycle.get_agent_state(conn) == AgentStateName.ACTIVE


def test_close_position_sell_direction_pnl(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(
            conn, [_card(action="sell", entry=100.0, stop_loss=101.0, take_profit=94.0)]
        )
        lifecycle.mark_filled(conn, card.signal_id, fill_price=100.0)
        pnl = lifecycle.close_position(conn, card.signal_id, close_price=94.0, reason="tp")
        assert pnl == pytest.approx(60.0)  # short: profits when price falls


def test_mark_filled_uses_entry_price_when_no_fill_price_given(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card(entry=123.45)])
        lifecycle.mark_filled(conn, card.signal_id)
        position = conn.execute(
            "SELECT entry_price FROM positions WHERE signal_id = ?", (card.signal_id,)
        ).fetchone()
        assert position["entry_price"] == 123.45


def test_mark_skipped_transitions_pending_signal(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card()])
        lifecycle.mark_skipped(conn, card.signal_id)
        row = lifecycle.get_signal(conn, card.signal_id)
        assert row["status"] == "skipped"


def test_mark_filled_twice_raises(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card()])
        lifecycle.mark_filled(conn, card.signal_id)
        with pytest.raises(lifecycle.LifecycleError):
            lifecycle.mark_filled(conn, card.signal_id)


def test_mark_skipped_after_filled_raises(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card()])
        lifecycle.mark_filled(conn, card.signal_id)
        with pytest.raises(lifecycle.LifecycleError):
            lifecycle.mark_skipped(conn, card.signal_id)


def test_close_position_without_open_position_raises(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card()])
        # Never filled -> no open position to close.
        with pytest.raises(lifecycle.LifecycleError):
            lifecycle.close_position(conn, card.signal_id, close_price=101.0)


def test_expire_due_signals_marks_only_past_due_pending_signals(seeded_db):
    with db.get_connection(seeded_db) as conn:
        past_due = _card(expires_at=datetime.now(UTC) - timedelta(hours=1))
        not_due = _card(expires_at=datetime.now(UTC) + timedelta(hours=1))
        [expired_card, live_card] = lifecycle.persist_cards(conn, [past_due, not_due])

        expired_ids = lifecycle.expire_due_signals(conn)

        assert expired_ids == [expired_card.signal_id]
        assert lifecycle.get_signal(conn, expired_card.signal_id)["status"] == "expired"
        assert lifecycle.get_signal(conn, live_card.signal_id)["status"] == "pending"


def test_expire_due_signals_never_touches_filled_signals(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(
            conn, [_card(expires_at=datetime.now(UTC) - timedelta(hours=1))]
        )
        lifecycle.mark_filled(conn, card.signal_id)
        expired_ids = lifecycle.expire_due_signals(conn)
        assert expired_ids == []
        assert lifecycle.get_signal(conn, card.signal_id)["status"] == "filled"


def test_refresh_agent_state_halts_on_daily_loss_limit(seeded_db, risk_config):
    with db.get_connection(seeded_db) as conn:
        # daily_loss_limit_percent=3.0 on £1000 => £30. Book a £40 loss today.
        [card] = lifecycle.persist_cards(
            conn, [_card(action="buy", entry=100.0, stop_loss=96.0, take_profit=112.0, units=10.0)]
        )
        lifecycle.mark_filled(conn, card.signal_id, fill_price=100.0)
        lifecycle.close_position(conn, card.signal_id, close_price=96.0, reason="sl")

        result = lifecycle.refresh_agent_state(conn, risk_config, risk_config.initial_bankroll)

        assert result.state == AgentStateName.HALTED_DAILY
        assert "daily loss" in result.reason
        assert lifecycle.get_agent_state(conn) == AgentStateName.HALTED_DAILY


def test_halt_and_resume_manual_control(seeded_db, risk_config):
    with db.get_connection(seeded_db) as conn:
        lifecycle.halt(conn, reason="testing halt")
        assert lifecycle.get_agent_state(conn) == AgentStateName.HALTED_DAILY

        result = lifecycle.resume(conn, risk_config, risk_config.initial_bankroll)
        # No losses booked, so resume clears the manual halt.
        assert result.state == AgentStateName.ACTIVE
        assert lifecycle.get_agent_state(conn) == AgentStateName.ACTIVE


def test_resume_stays_halted_if_breaker_still_breached(seeded_db, risk_config):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(
            conn, [_card(action="buy", entry=100.0, stop_loss=96.0, take_profit=112.0, units=10.0)]
        )
        lifecycle.mark_filled(conn, card.signal_id, fill_price=100.0)
        lifecycle.close_position(conn, card.signal_id, close_price=96.0, reason="sl")
        lifecycle.halt(conn, reason="acknowledging loss")

        result = lifecycle.resume(conn, risk_config, risk_config.initial_bankroll)
        assert result.state == AgentStateName.HALTED_DAILY
        assert "daily loss" in result.reason


def test_mark_to_market_includes_unrealized_pnl_from_open_positions(seeded_db, risk_config):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(
            conn, [_card(action="buy", entry=100.0, stop_loss=96.0, take_profit=112.0, units=10.0)]
        )
        lifecycle.mark_filled(conn, card.signal_id, fill_price=100.0)

        # Realized P&L is still zero (position open); a live quote showing a
        # £40 unrealized loss should still be enough to trip the daily breaker.
        market = _FakeMarket(quotes={"AAPL": 96.0})
        result = lifecycle.mark_to_market(conn, risk_config, risk_config.initial_bankroll, market)

        assert result.state == AgentStateName.HALTED_DAILY
        assert "daily loss" in result.reason
        assert lifecycle.get_agent_state(conn) == AgentStateName.HALTED_DAILY


def test_mark_to_market_stays_active_when_unrealized_pnl_is_small(seeded_db, risk_config):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(
            conn, [_card(action="buy", entry=100.0, stop_loss=96.0, take_profit=112.0, units=10.0)]
        )
        lifecycle.mark_filled(conn, card.signal_id, fill_price=100.0)

        market = _FakeMarket(quotes={"AAPL": 99.5})
        result = lifecycle.mark_to_market(conn, risk_config, risk_config.initial_bankroll, market)

        assert result.state == AgentStateName.ACTIVE
