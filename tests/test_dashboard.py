"""Tests for the pure, streamlit-free data-loading helpers behind the
read-only dashboard. `ui/dashboard.py` only imports `streamlit` inside
`main()`, so these functions are testable without it installed, and none of
these tests ever call a lifecycle writer through the dashboard module —
confirming the dashboard has no write path of its own.
"""
from datetime import UTC, datetime, timedelta

import pytest

from alphatrader import db
from alphatrader.models import Quote
from alphatrader.signals import lifecycle
from alphatrader.signals.cards import SignalCard
from alphatrader.ui.dashboard import (
    equity_curve,
    load_open_positions,
    load_open_signals,
    load_recent_signals,
)


@pytest.fixture
def seeded_db(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


def _card(**overrides) -> SignalCard:
    defaults = dict(
        signal_id=999,
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


class _FakeMarket:
    def __init__(self, quotes: dict[str, float]):
        self._quotes = quotes

    def get_quote(self, symbol: str) -> Quote:
        return Quote(symbol=symbol, price=self._quotes[symbol], timestamp=datetime.now(UTC))


def test_load_open_signals_returns_only_pending(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [pending, skipped] = lifecycle.persist_cards(
            conn, [_card(symbol="AAPL"), _card(symbol="MSFT")]
        )
        lifecycle.mark_skipped(conn, skipped.signal_id)

        out = load_open_signals(conn)

        assert list(out["symbol"]) == ["AAPL"]
        assert out.iloc[0]["id"] == pending.signal_id


def test_load_recent_signals_orders_newest_first_and_respects_limit(seeded_db):
    with db.get_connection(seeded_db) as conn:
        lifecycle.persist_cards(conn, [_card(symbol="AAPL"), _card(symbol="MSFT")])

        out = load_recent_signals(conn, limit=1)

        assert len(out) == 1
        assert out.iloc[0]["symbol"] == "MSFT"


def test_load_open_positions_includes_unrealized_pnl_from_market(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(
            conn, [_card(action="buy", entry=100.0, stop_loss=96.0, take_profit=112.0, units=10.0)]
        )
        lifecycle.mark_filled(conn, card.signal_id, fill_price=100.0)

        out = load_open_positions(conn, _FakeMarket(quotes={"AAPL": 103.0}))

        assert len(out) == 1
        assert out.iloc[0]["unrealized_pnl_gbp"] == pytest.approx(30.0)


def test_load_open_positions_reports_none_when_market_unavailable(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card()])
        lifecycle.mark_filled(conn, card.signal_id, fill_price=100.0)

        out = load_open_positions(conn, None)

        assert out.iloc[0]["unrealized_pnl_gbp"] is None


def test_load_open_positions_excludes_closed_positions(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card()])
        lifecycle.mark_filled(conn, card.signal_id, fill_price=100.0)
        lifecycle.close_position(conn, card.signal_id, close_price=106.0, reason="tp")

        out = load_open_positions(conn, None)

        assert out.empty


def test_equity_curve_starts_at_bankroll_and_accumulates_realized_pnl(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card()])
        lifecycle.mark_filled(conn, card.signal_id, fill_price=100.0)
        lifecycle.close_position(conn, card.signal_id, close_price=106.0, reason="tp")

        out = equity_curve(conn, initial_bankroll=1000.0)

        assert out.iloc[0]["equity_gbp"] == 1000.0
        assert out.iloc[-1]["equity_gbp"] == pytest.approx(1060.0)


def test_equity_curve_is_flat_with_no_closed_positions(seeded_db):
    with db.get_connection(seeded_db) as conn:
        out = equity_curve(conn, initial_bankroll=1000.0)

        assert len(out) == 1
        assert out.iloc[0]["equity_gbp"] == 1000.0
