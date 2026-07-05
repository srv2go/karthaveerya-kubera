from datetime import UTC, datetime

from alphatrader.signals.cards import SignalCard, render


def _card(**overrides) -> SignalCard:
    defaults = dict(
        signal_id=3,
        symbol="AAPL",
        action="buy",
        entry=231.40,
        stop_loss=227.10,
        take_profit=240.10,
        units=0.83,
        risk_gbp=10.00,
        amount_gbp=192.0,
        risk_reward=2.0,
        spread_multiple=5.4,
        rationale="Uptrend continuation; could fail if support breaks.",
        expires_at=datetime(2024, 1, 1, 11, 30, tzinfo=UTC),
        initial_bankroll=1000.0,
        risk_pct=1.0,
        instrument_class="stock",
    )
    defaults.update(overrides)
    return SignalCard(**defaults)


def test_render_buy_card_matches_expected_shape():
    text = render(_card())
    assert "🟢 SIGNAL #3 — BUY AAPL" in text
    assert "(expires 11:30 UTC)" in text
    assert "Entry ......... 231.40" in text
    assert "Stop-loss ..... 227.10   (-1.86%)" in text
    assert "Take-profit ... 240.10   (R/R 2.0)" in text
    assert "eToro amount .. £192" in text
    assert "Planned risk .. £10.00 (1.0% of £1000)" in text
    assert "Note: set leverage to X1" in text
    assert "⚠ Signal, not advice — you decide.  [I placed it] [Skip]" in text


def test_render_sell_card_uses_red_emoji_and_no_leverage_note_for_crypto():
    text = render(_card(action="sell", instrument_class="crypto", symbol="BTC/USD"))
    assert "🔴 SIGNAL #3 — SELL BTC/USD" in text
    assert "Note: set leverage to X1" not in text
