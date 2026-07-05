"""Daily (21:00 UTC) and weekly (Sunday) reports.

Pure read-only summaries of the ledger and signal history. Nothing here
writes to the DB or talks to a broker; the scheduler in `__main__.py` sends
the resulting text via Telegram.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from alphatrader.data.market import MarketDataService
from alphatrader.ledger import position_pnl
from alphatrader.models import Action, Candle, SignalStatus
from alphatrader.risk.engine import RiskConfig
from alphatrader.risk.state import evaluate_breakers
from alphatrader.signals import lifecycle

WEEKLY_TARGET_LABEL = "aspirational target — not a performance promise"


def _day_start(now: datetime) -> datetime:
    return datetime(now.year, now.month, now.day, tzinfo=UTC)


def _week_start(now: datetime) -> datetime:
    return _day_start(now) - timedelta(days=now.weekday())


def _first_hit(
    action: Action, stop_loss: float, take_profit: float, candle: Candle
) -> float | None:
    """Return the exit price if this bar hits the stop or target, else None.

    Conservative tie-break: if both are hit in the same bar, the stop wins.
    """
    if action == Action.BUY:
        if candle.low <= stop_loss:
            return stop_loss
        if candle.high >= take_profit:
            return take_profit
    else:
        if candle.high >= stop_loss:
            return stop_loss
        if candle.low <= take_profit:
            return take_profit
    return None


def _period_realized(conn: sqlite3.Connection, since: datetime) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(realized_pnl_gbp), 0) AS realized FROM positions "
        "WHERE closed_at IS NOT NULL AND closed_at >= ?",
        (since.isoformat(),),
    ).fetchone()
    return row["realized"]


def _closed_position_stats(conn: sqlite3.Connection, since: datetime) -> tuple[int, int, float]:
    """Return (total_closed, wins, avg_r_multiple) for positions closed since `since`."""
    rows = conn.execute(
        "SELECT realized_pnl_gbp, risk_gbp FROM positions "
        "WHERE closed_at IS NOT NULL AND closed_at >= ?",
        (since.isoformat(),),
    ).fetchall()
    if not rows:
        return 0, 0, 0.0
    wins = sum(1 for r in rows if r["realized_pnl_gbp"] > 0)
    r_multiples = [r["realized_pnl_gbp"] / r["risk_gbp"] for r in rows if r["risk_gbp"]]
    avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0
    return len(rows), wins, avg_r


def _signal_status_counts(conn: sqlite3.Connection, since: datetime) -> dict[str, int]:
    counts = {status.value: 0 for status in SignalStatus}
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM signals WHERE created_at >= ? GROUP BY status",
        (since.isoformat(),),
    ).fetchall()
    for row in rows:
        counts[row["status"]] = row["n"]
    return counts


def build_daily_report(
    conn: sqlite3.Connection,
    risk_cfg: RiskConfig,
    initial_bankroll: float,
    market: MarketDataService | None = None,
    now: datetime | None = None,
) -> str:
    """Realized/unrealized P&L, signal funnel, win rate, R-multiple, distance
    to breakers, and progress vs the weekly aspirational target.
    """
    now = now or datetime.now(UTC)
    day_start = _day_start(now)
    week_start = _week_start(now)

    balance = lifecycle.current_balance_gbp(conn, initial_bankroll)
    all_time_realized = _period_realized(conn, datetime.min.replace(tzinfo=UTC))
    day_start_balance = initial_bankroll + (all_time_realized - _period_realized(conn, day_start))
    week_start_balance = initial_bankroll + (all_time_realized - _period_realized(conn, week_start))

    open_positions = conn.execute(
        "SELECT symbol, action, units, entry_price FROM positions WHERE closed_at IS NULL"
    ).fetchall()
    unrealized = 0.0
    if market is not None:
        for pos in open_positions:
            try:
                price = market.get_quote(pos["symbol"]).price
            except Exception:
                continue
            unrealized += position_pnl(
                Action(pos["action"]), pos["units"], pos["entry_price"], price
            )
    equity = balance + unrealized

    breaker = evaluate_breakers(risk_cfg, equity, day_start_balance, week_start_balance)

    daily_limit = day_start_balance * risk_cfg.daily_loss_limit_percent / 100.0
    daily_room = daily_limit - (day_start_balance - equity)
    weekly_limit = week_start_balance * risk_cfg.weekly_loss_limit_percent / 100.0
    weekly_room = weekly_limit - (week_start_balance - equity)
    floor_room = equity - risk_cfg.cash_preservation_floor_gbp

    total_closed, wins, avg_r = _closed_position_stats(conn, week_start)
    win_rate = (wins / total_closed * 100.0) if total_closed else 0.0

    counts = _signal_status_counts(conn, day_start)

    week_realized = _period_realized(conn, week_start)
    progress_pct = (
        week_realized / risk_cfg.weekly_profit_target_gbp * 100.0
        if risk_cfg.weekly_profit_target_gbp
        else 0.0
    )

    lines = [
        f"Daily report — {now.date().isoformat()}",
        f"Realized P&L (today's balance): £{balance:.2f}  Unrealized: £{unrealized:.2f}",
        f"Equity: £{equity:.2f}  Agent state: {breaker.state.value}"
        + (f" ({breaker.reason})" if breaker.reason else ""),
        f"Signals today — pending: {counts['pending']}, filled: {counts['filled']}, "
        f"skipped: {counts['skipped']}, expired: {counts['expired']}, closed: {counts['closed']}",
        f"Week win rate: {win_rate:.0f}% ({wins}/{total_closed})  Avg R-multiple: {avg_r:.2f}",
        f"Room to daily breaker: £{daily_room:.2f}  Room to weekly breaker: £{weekly_room:.2f}  "
        f"Room to cash floor: £{floor_room:.2f}",
        f"Week realized P&L £{week_realized:.2f} vs £{risk_cfg.weekly_profit_target_gbp:.0f}/week "
        f"({WEEKLY_TARGET_LABEL}): {progress_pct:.0f}%",
    ]
    return "\n".join(lines)


def build_weekly_review(
    conn: sqlite3.Connection,
    market: MarketDataService,
    now: datetime | None = None,
) -> str:
    """Compare skipped signals' hypothetical outcomes to taken (filled) ones
    over the past week, using recent candles to determine which of
    stop-loss/take-profit would have been hit first.
    """
    now = now or datetime.now(UTC)
    since = now - timedelta(days=7)

    rows = conn.execute(
        "SELECT * FROM signals WHERE created_at >= ? AND status IN (?, ?, ?) ORDER BY created_at",
        (
            since.isoformat(),
            SignalStatus.SKIPPED.value,
            SignalStatus.FILLED.value,
            SignalStatus.CLOSED.value,
        ),
    ).fetchall()

    skipped_pnl_total = 0.0
    skipped_count = 0
    skipped_undetermined = 0
    taken_pnl_total = 0.0
    taken_count = 0

    for row in rows:
        created_at = datetime.fromisoformat(row["created_at"])
        if row["status"] in (SignalStatus.FILLED.value, SignalStatus.CLOSED.value):
            position = conn.execute(
                "SELECT realized_pnl_gbp FROM positions "
                "WHERE signal_id = ? AND closed_at IS NOT NULL",
                (row["id"],),
            ).fetchone()
            if position is not None:
                taken_pnl_total += position["realized_pnl_gbp"]
                taken_count += 1
            continue

        # Skipped: replay recent candles after signal creation to see whether
        # the stop or the target would have been hit first.
        try:
            candles = market.get_candles(row["symbol"])
        except Exception:
            skipped_undetermined += 1
            continue
        action = Action(row["action"])
        outcome = None
        for candle in candles:
            if candle.timestamp <= created_at:
                continue
            exit_price = _first_hit(action, row["stop_loss"], row["take_profit"], candle)
            if exit_price is not None:
                outcome = position_pnl(action, row["units"], row["entry"], exit_price)
                break
        if outcome is None:
            skipped_undetermined += 1
        else:
            skipped_pnl_total += outcome
            skipped_count += 1

    lines = [
        f"Weekly review — signals from {since.date().isoformat()} to {now.date().isoformat()}",
        f"Taken (filled+closed): {taken_count} signals, realized P&L £{taken_pnl_total:.2f}",
        f"Skipped: {skipped_count} resolved (£{skipped_pnl_total:.2f} hypothetical P&L), "
        f"{skipped_undetermined} undetermined (not enough recent candle history)",
    ]
    if skipped_count and taken_count:
        verdict = (
            "skipping added value this week"
            if skipped_pnl_total < 0 and taken_pnl_total >= 0
            else "review individually — no clear signal from this small a sample"
        )
        lines.append(f"Note: {verdict}.")
    return "\n".join(lines)
