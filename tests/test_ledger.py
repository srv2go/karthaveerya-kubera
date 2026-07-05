from alphatrader.ledger import Ledger
from alphatrader.models import Action
from alphatrader.risk.state import evaluate_breakers


def test_open_and_close_winning_buy_position(risk_config):
    ledger = Ledger(balance_gbp=1000.0)
    pos = ledger.open_position(
        signal_id=1, symbol="AAPL", action=Action.BUY, units=1.0,
        fill_price=100.0, stop_loss=99.0, take_profit=103.0, risk_gbp=1.0,
    )
    pnl = ledger.close_position(pos.id, close_price=103.0)
    assert pnl == 3.0
    assert ledger.balance_gbp == 1003.0
    assert ledger.realized_pnl_gbp == 3.0


def test_open_and_close_losing_sell_position():
    ledger = Ledger(balance_gbp=1000.0)
    pos = ledger.open_position(
        signal_id=2, symbol="BTC/USD", action=Action.SELL, units=0.01,
        fill_price=50_000.0, stop_loss=51_000.0, take_profit=47_000.0, risk_gbp=10.0,
    )
    # Price rose against a short -> loss
    pnl = ledger.close_position(pos.id, close_price=51_000.0)
    assert pnl == -10.0
    assert ledger.balance_gbp == 990.0


def test_unrealized_pnl_and_drawdown_mark_to_market():
    ledger = Ledger(balance_gbp=1000.0)
    ledger.open_position(
        signal_id=1, symbol="AAPL", action=Action.BUY, units=2.0,
        fill_price=100.0, stop_loss=98.0, take_profit=106.0, risk_gbp=4.0,
    )
    equity = ledger.mark_to_market({"AAPL": 90.0})  # price dropped
    assert equity == 1000.0 - 20.0
    assert ledger.drawdown_pct({"AAPL": 90.0}) > 0


def test_drawdown_zero_at_new_high():
    ledger = Ledger(balance_gbp=1000.0)
    ledger.open_position(
        signal_id=1, symbol="AAPL", action=Action.BUY, units=1.0,
        fill_price=100.0, stop_loss=98.0, take_profit=110.0, risk_gbp=2.0,
    )
    ledger.mark_to_market({"AAPL": 105.0})
    assert ledger.drawdown_pct({"AAPL": 105.0}) == 0.0


def test_breaker_trips_on_daily_loss(risk_config):
    ledger = Ledger(balance_gbp=1000.0)
    ledger.balance_gbp = 965.0  # simulate a 3.5% realized loss today
    result = evaluate_breakers(
        risk_config,
        equity_gbp=ledger.equity_gbp({}),
        day_start_balance_gbp=1000.0,
        week_start_balance_gbp=1000.0,
    )
    assert result.state.value == "HALTED_DAILY"


def test_breaker_trips_on_preservation_floor(risk_config):
    ledger = Ledger(balance_gbp=1000.0)
    ledger.balance_gbp = 900.0  # below the 940 floor
    result = evaluate_breakers(
        risk_config,
        equity_gbp=ledger.equity_gbp({}),
        day_start_balance_gbp=1000.0,
        week_start_balance_gbp=1000.0,
    )
    assert result.state.value == "PRESERVATION"


def test_no_breaker_when_within_limits(risk_config):
    ledger = Ledger(balance_gbp=1000.0)
    ledger.balance_gbp = 995.0
    result = evaluate_breakers(
        risk_config,
        equity_gbp=ledger.equity_gbp({}),
        day_start_balance_gbp=1000.0,
        week_start_balance_gbp=1000.0,
    )
    assert result.state.value == "ACTIVE"
