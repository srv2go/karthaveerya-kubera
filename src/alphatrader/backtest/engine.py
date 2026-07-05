"""Backtest engine: replays the same RiskEngine over historical OHLCV data.

The strategy in `backtest/strategies.py` stands in for the LLM analyst, but
every proposal it produces still passes through `risk/engine.py::evaluate()`
for validation and sizing, and through `risk/state.py::evaluate_breakers()`
for circuit-breaker gating — exactly as in the live HITL pipeline. There is
no execution client here either; positions are simulated in memory only.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from alphatrader.backtest import strategies
from alphatrader.ledger import position_pnl
from alphatrader.models import Action, AgentStateName, Candle, TradeProposal
from alphatrader.risk.engine import RiskConfig, evaluate
from alphatrader.risk.state import evaluate_breakers

Strategy = Callable[[str, list[Candle]], TradeProposal]


@dataclass
class Trade:
    symbol: str
    action: Action
    entry_time: datetime
    entry_price: float
    stop_loss: float
    take_profit: float
    units: float
    risk_gbp: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    pnl_gbp: float | None = None


@dataclass
class BacktestResult:
    initial_balance_gbp: float
    final_balance_gbp: float
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    halts: list[tuple[datetime, str]] = field(default_factory=list)


def _check_exit(trade: Trade, candle: Candle) -> tuple[float, str] | None:
    """Return (exit_price, reason) if this bar hits the stop or target.

    Conservative tie-break: if both stop and target are hit within the same
    bar, the stop is assumed to have been hit first.
    """
    if trade.action == Action.BUY:
        if candle.low <= trade.stop_loss:
            return trade.stop_loss, "stop_loss"
        if candle.high >= trade.take_profit:
            return trade.take_profit, "take_profit"
    else:
        if candle.high >= trade.stop_loss:
            return trade.stop_loss, "stop_loss"
        if candle.low <= trade.take_profit:
            return trade.take_profit, "take_profit"
    return None


def run_backtest(
    symbol: str,
    candles: list[Candle],
    cfg: RiskConfig,
    typical_spread_bps: float = 3.0,
    strategy: Strategy = strategies.propose,
) -> BacktestResult:
    """Replay `strategy` + the live RiskEngine/breakers over `candles`.

    At most one open position at a time (this is a single-symbol backtest).
    Fills happen instantly at the proposal's entry price — there is no HITL
    confirmation delay to simulate here, unlike the live Telegram flow.
    """
    balance = cfg.initial_bankroll
    day_start_balance = balance
    week_start_balance = balance
    current_day = None
    current_week = None
    open_trade: Trade | None = None
    agent_state = AgentStateName.ACTIVE

    trades: list[Trade] = []
    equity_curve: list[tuple[datetime, float]] = []
    halts: list[tuple[datetime, str]] = []

    for i, candle in enumerate(candles):
        day_key = candle.timestamp.date()
        week_key = candle.timestamp.isocalendar()[:2]
        if current_day is None:
            current_day = day_key
        elif day_key != current_day:
            day_start_balance = balance
            current_day = day_key
        if current_week is None:
            current_week = week_key
        elif week_key != current_week:
            week_start_balance = balance
            current_week = week_key

        if open_trade is not None:
            hit = _check_exit(open_trade, candle)
            if hit is not None:
                exit_price, reason = hit
                pnl = position_pnl(
                    open_trade.action, open_trade.units, open_trade.entry_price, exit_price
                )
                open_trade.exit_time = candle.timestamp
                open_trade.exit_price = exit_price
                open_trade.exit_reason = reason
                open_trade.pnl_gbp = pnl
                balance += pnl
                trades.append(open_trade)
                open_trade = None

        if open_trade is not None:
            equity = balance + position_pnl(
                open_trade.action, open_trade.units, open_trade.entry_price, candle.close
            )
        else:
            equity = balance
        equity_curve.append((candle.timestamp, equity))

        breaker = evaluate_breakers(cfg, equity, day_start_balance, week_start_balance)
        if breaker.state != agent_state and breaker.state != AgentStateName.ACTIVE:
            halts.append((candle.timestamp, f"{breaker.state.value}: {breaker.reason}"))
        agent_state = breaker.state

        if agent_state == AgentStateName.ACTIVE and open_trade is None:
            proposal = strategy(symbol, candles[: i + 1])
            if proposal.action != Action.HOLD:
                verdict = evaluate(proposal, cfg, balance, typical_spread_bps, 0, agent_state)
                if verdict.accepted:
                    open_trade = Trade(
                        symbol=symbol,
                        action=proposal.action,
                        entry_time=candle.timestamp,
                        entry_price=proposal.entry,
                        stop_loss=proposal.stop_loss,
                        take_profit=proposal.take_profit,
                        units=verdict.units,
                        risk_gbp=verdict.risk_gbp,
                    )

    if open_trade is not None and candles:
        last = candles[-1]
        pnl = position_pnl(open_trade.action, open_trade.units, open_trade.entry_price, last.close)
        open_trade.exit_time = last.timestamp
        open_trade.exit_price = last.close
        open_trade.exit_reason = "end_of_backtest"
        open_trade.pnl_gbp = pnl
        balance += pnl
        trades.append(open_trade)
        if equity_curve:
            equity_curve[-1] = (equity_curve[-1][0], balance)

    return BacktestResult(
        initial_balance_gbp=cfg.initial_bankroll,
        final_balance_gbp=balance,
        trades=trades,
        equity_curve=equity_curve,
        halts=halts,
    )
