from datetime import UTC, datetime, timedelta

import pytest

from alphatrader import db
from alphatrader.models import Candle, Quote
from alphatrader.reporting.daily import WEEKLY_TARGET_LABEL, build_daily_report, build_weekly_review
from alphatrader.signals import lifecycle
from alphatrader.signals.cards import SignalCard


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
    def __init__(self, candles: dict[str, list[Candle]], quotes: dict[str, float] | None = None):
        self._candles = candles
        self._quotes = quotes or {}

    def get_candles(self, symbol: str, limit: int = 60) -> list[Candle]:
        return self._candles.get(symbol, [])

    def get_quote(self, symbol: str) -> Quote:
        price = self._quotes[symbol]
        return Quote(symbol=symbol, price=price, timestamp=datetime.now(UTC))


def test_daily_report_reflects_realized_pnl_and_signal_counts(seeded_db, risk_config):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card()])
        lifecycle.mark_filled(conn, card.signal_id, fill_price=100.0)
        lifecycle.close_position(conn, card.signal_id, close_price=106.0, reason="tp")

        report = build_daily_report(conn, risk_config, risk_config.initial_bankroll)

        assert "£1060.00" in report
        assert "closed: 1" in report
        assert "Agent state: ACTIVE" in report
        assert WEEKLY_TARGET_LABEL in report


def test_daily_report_shows_halted_state_and_reason(seeded_db, risk_config):
    with db.get_connection(seeded_db) as conn:
        # daily_loss_limit_percent=3.0 on £1000 => £30. Book a £40 loss today.
        [card] = lifecycle.persist_cards(
            conn, [_card(action="buy", entry=100.0, stop_loss=96.0, take_profit=112.0, units=10.0)]
        )
        lifecycle.mark_filled(conn, card.signal_id, fill_price=100.0)
        lifecycle.close_position(conn, card.signal_id, close_price=96.0, reason="sl")

        report = build_daily_report(conn, risk_config, risk_config.initial_bankroll)

        assert "HALTED_DAILY" in report
        assert "daily loss" in report


def test_daily_report_includes_unrealized_pnl_from_market_quotes(seeded_db, risk_config):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card()])
        lifecycle.mark_filled(conn, card.signal_id, fill_price=100.0)

        market = _FakeMarket(candles={}, quotes={"AAPL": 103.0})
        report = build_daily_report(
            conn, risk_config, risk_config.initial_bankroll, market=market
        )

        assert "Unrealized: £30.00" in report


def test_weekly_review_reports_taken_signal_pnl(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card()])
        lifecycle.mark_filled(conn, card.signal_id, fill_price=100.0)
        lifecycle.close_position(conn, card.signal_id, close_price=106.0, reason="tp")

        market = _FakeMarket(candles={})
        review = build_weekly_review(conn, market)

        assert "Taken (filled+closed): 1 signals, realized P&L £60.00" in review


def test_weekly_review_resolves_skipped_signal_hypothetical_outcome(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card()])
        lifecycle.mark_skipped(conn, card.signal_id)
        signal = lifecycle.get_signal(conn, card.signal_id)
        created_at = datetime.fromisoformat(signal["created_at"])

        candles = [
            Candle(
                symbol="AAPL",
                timestamp=created_at + timedelta(hours=1),
                open=100.0,
                high=107.0,
                low=99.5,
                close=106.5,
                volume=1000.0,
            )
        ]
        market = _FakeMarket(candles={"AAPL": candles})
        review = build_weekly_review(conn, market)

        assert "Skipped: 1 resolved (£60.00 hypothetical P&L), 0 undetermined" in review


def test_weekly_review_marks_skipped_undetermined_without_candle_history(seeded_db):
    with db.get_connection(seeded_db) as conn:
        [card] = lifecycle.persist_cards(conn, [_card()])
        lifecycle.mark_skipped(conn, card.signal_id)

        market = _FakeMarket(candles={})
        review = build_weekly_review(conn, market)

        assert "Skipped: 0 resolved (£0.00 hypothetical P&L), 1 undetermined" in review
