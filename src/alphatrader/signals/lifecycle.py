"""Signal & position lifecycle state machine, backed directly by SQLite.

State machine: signals go `pending` -> `filled` | `skipped` | `expired`;
once filled, the corresponding position goes open -> `closed`. This module
is the single write path to the ledger (via `positions.realized_pnl_gbp`)
and to `agent_state` — there is no other code path that changes either.
"""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from alphatrader.ledger import position_pnl
from alphatrader.models import Action, AgentStateName, SignalStatus
from alphatrader.risk.engine import RiskConfig
from alphatrader.risk.state import BreakerResult, evaluate_breakers
from alphatrader.signals.cards import SignalCard


class LifecycleError(ValueError):
    """Raised for invalid state transitions (e.g. filling an already-closed signal)."""


def persist_cards(conn: sqlite3.Connection, cards: list[SignalCard]) -> list[SignalCard]:
    """Insert freshly-scanned cards as `pending` signals and return copies
    with their real database ids (replacing the pipeline's placeholder ids).
    """
    now = datetime.now(UTC).isoformat()
    persisted: list[SignalCard] = []
    for card in cards:
        cur = conn.execute(
            "INSERT INTO signals (symbol, action, entry, stop_loss, take_profit, units, "
            "risk_gbp, amount_gbp, risk_reward, confidence, rationale, status, "
            "created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                card.symbol, card.action, card.entry, card.stop_loss, card.take_profit,
                card.units, card.risk_gbp, card.amount_gbp, card.risk_reward,
                card.confidence, card.rationale, SignalStatus.PENDING.value,
                now, card.expires_at.isoformat(),
            ),
        )
        persisted.append(replace(card, signal_id=cur.lastrowid))
    conn.commit()
    return persisted


def get_signal(conn: sqlite3.Connection, signal_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if row is None:
        raise LifecycleError(f"no such signal: {signal_id}")
    return row


def mark_filled(conn: sqlite3.Connection, signal_id: int, fill_price: float | None = None) -> None:
    """Transition pending -> filled and open the corresponding position."""
    signal = get_signal(conn, signal_id)
    if signal["status"] != SignalStatus.PENDING.value:
        raise LifecycleError(
            f"signal {signal_id} is {signal['status']!r}, cannot mark filled (must be pending)"
        )
    price = fill_price if fill_price is not None else signal["entry"]
    now = datetime.now(UTC).isoformat()

    conn.execute(
        "UPDATE signals SET status = ?, fill_price = ? WHERE id = ?",
        (SignalStatus.FILLED.value, price, signal_id),
    )
    conn.execute(
        "INSERT INTO fills (signal_id, fill_price, filled_at) VALUES (?, ?, ?)",
        (signal_id, price, now),
    )
    conn.execute(
        "INSERT INTO positions (signal_id, symbol, action, units, entry_price, stop_loss, "
        "take_profit, risk_gbp, opened_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            signal_id, signal["symbol"], signal["action"], signal["units"], price,
            signal["stop_loss"], signal["take_profit"], signal["risk_gbp"], now,
        ),
    )
    conn.commit()


def mark_skipped(conn: sqlite3.Connection, signal_id: int) -> None:
    signal = get_signal(conn, signal_id)
    if signal["status"] != SignalStatus.PENDING.value:
        raise LifecycleError(
            f"signal {signal_id} is {signal['status']!r}, cannot mark skipped (must be pending)"
        )
    conn.execute(
        "UPDATE signals SET status = ? WHERE id = ?", (SignalStatus.SKIPPED.value, signal_id)
    )
    conn.commit()


def expire_due_signals(conn: sqlite3.Connection, now: datetime | None = None) -> list[int]:
    """Expire every pending signal whose expires_at has passed. Returns the
    list of expired signal ids. Never assumes an unconfirmed signal was filled.
    """
    now = now or datetime.now(UTC)
    rows = conn.execute(
        "SELECT id FROM signals WHERE status = ? AND expires_at IS NOT NULL AND expires_at <= ?",
        (SignalStatus.PENDING.value, now.isoformat()),
    ).fetchall()
    ids = [row["id"] for row in rows]
    if ids:
        conn.executemany(
            "UPDATE signals SET status = ? WHERE id = ?",
            [(SignalStatus.EXPIRED.value, signal_id) for signal_id in ids],
        )
        conn.commit()
    return ids


def close_position(
    conn: sqlite3.Connection, signal_id: int, close_price: float, reason: str = ""
) -> float:
    """Close the open position for `signal_id`, book realized P&L, and mark
    the signal closed. Returns the realized P&L in GBP.
    """
    get_signal(conn, signal_id)  # raises LifecycleError if the signal itself doesn't exist
    position = conn.execute(
        "SELECT * FROM positions WHERE signal_id = ? AND closed_at IS NULL", (signal_id,)
    ).fetchone()
    if position is None:
        raise LifecycleError(f"no open position for signal {signal_id}")

    pnl = position_pnl(
        Action(position["action"]), position["units"], position["entry_price"], close_price
    )
    now = datetime.now(UTC).isoformat()

    conn.execute(
        "UPDATE positions SET closed_at = ?, close_price = ?, realized_pnl_gbp = ? WHERE id = ?",
        (now, close_price, pnl, position["id"]),
    )
    conn.execute(
        "UPDATE signals SET status = ?, close_price = ?, close_reason = ? WHERE id = ?",
        (SignalStatus.CLOSED.value, close_price, reason, signal_id),
    )
    conn.commit()
    return pnl


def current_balance_gbp(conn: sqlite3.Connection, initial_bankroll: float) -> float:
    """Realized-only balance: initial bankroll plus all booked realized P&L."""
    row = conn.execute(
        "SELECT COALESCE(SUM(realized_pnl_gbp), 0) AS realized "
        "FROM positions WHERE closed_at IS NOT NULL"
    ).fetchone()
    return initial_bankroll + row["realized"]


def _period_start_balance(conn: sqlite3.Connection, initial_bankroll: float,
                           since: datetime) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(realized_pnl_gbp), 0) AS realized FROM positions "
        "WHERE closed_at IS NOT NULL AND closed_at < ?",
        (since.isoformat(),),
    ).fetchone()
    return initial_bankroll + row["realized"]


def get_agent_state(conn: sqlite3.Connection) -> AgentStateName:
    row = conn.execute("SELECT state FROM agent_state WHERE id = 1").fetchone()
    return AgentStateName(row["state"])


def refresh_agent_state(
    conn: sqlite3.Connection,
    risk_cfg: RiskConfig,
    initial_bankroll: float,
    now: datetime | None = None,
) -> BreakerResult:
    """Recompute breaker state from realized P&L booked via /closed
    confirmations, and persist it to `agent_state`. Note: this uses
    realized-only equity; the 15-min intraday mark-to-market job (Phase 7)
    additionally feeds live prices into the same breaker check.
    """
    now = now or datetime.now(UTC)
    day_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    week_start = day_start - timedelta(days=now.weekday())

    balance = current_balance_gbp(conn, initial_bankroll)
    day_start_balance = _period_start_balance(conn, initial_bankroll, day_start)
    week_start_balance = _period_start_balance(conn, initial_bankroll, week_start)

    result = evaluate_breakers(risk_cfg, balance, day_start_balance, week_start_balance)
    conn.execute(
        "UPDATE agent_state SET state = ?, reason = ?, updated_at = ? WHERE id = 1",
        (result.state.value, result.reason, now.isoformat()),
    )
    conn.commit()
    return result


def halt(
    conn: sqlite3.Connection, reason: str = "manual halt", now: datetime | None = None
) -> None:
    """Manual /halt: stop issuing signals immediately, regardless of breaker math."""
    now = now or datetime.now(UTC)
    conn.execute(
        "UPDATE agent_state SET state = ?, reason = ?, updated_at = ? WHERE id = 1",
        (AgentStateName.HALTED_DAILY.value, f"manual: {reason}", now.isoformat()),
    )
    conn.commit()


def resume(
    conn: sqlite3.Connection,
    risk_cfg: RiskConfig,
    initial_bankroll: float,
    now: datetime | None = None,
) -> BreakerResult:
    """Manual /resume: re-evaluate breakers. If the underlying loss limits
    or floor are still breached, the agent stays halted with the real reason.
    """
    return refresh_agent_state(conn, risk_cfg, initial_bankroll, now=now)
