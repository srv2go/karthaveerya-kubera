"""Read-only Streamlit dashboard: pending signal cards, open positions,
the realized-equity curve, and the weekly skipped-vs-taken review — all
read directly from the SQLite ledger.

Run with:
    streamlit run src/alphatrader/ui/dashboard.py

Strictly read-only. This module only ever executes SELECT queries (plus one
live market-data read, used solely to display unrealized P&L and the weekly
review). It never calls `persist_cards`, `mark_filled`, `mark_skipped`,
`close_position`, `halt`, `resume`, or any other lifecycle writer — Telegram
remains the single confirmation path, so `signals/lifecycle.py` stays the
only write path to the ledger. There is no button here that fills, skips,
closes, halts, or resumes anything.

`streamlit` is an optional dependency (`pip install -e ".[dashboard]"`); the
data-loading functions below have no import-time dependency on it so they
stay unit-testable without it installed. Only `main()` imports `streamlit`,
and it is only invoked when this file is run directly (which is how
`streamlit run` executes it).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pandas as pd

from alphatrader.data.market import MarketDataService
from alphatrader.db import get_connection
from alphatrader.ledger import position_pnl
from alphatrader.models import Action
from alphatrader.reporting.daily import build_weekly_review
from alphatrader.risk.engine import load_risk_config
from alphatrader.settings import Settings, get_settings
from alphatrader.signals import lifecycle


def load_open_signals(conn: sqlite3.Connection) -> pd.DataFrame:
    """Pending signal cards awaiting a Telegram [I placed it] / [Skip]."""
    rows = conn.execute(
        "SELECT id, symbol, action, entry, stop_loss, take_profit, risk_gbp, "
        "amount_gbp, risk_reward, confidence, created_at, expires_at "
        "FROM signals WHERE status = 'pending' ORDER BY created_at DESC, id DESC"
    ).fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def load_recent_signals(conn: sqlite3.Connection, limit: int = 20) -> pd.DataFrame:
    """The most recent signals of any status, newest first."""
    rows = conn.execute(
        "SELECT id, symbol, action, status, entry, fill_price, close_price, "
        "risk_reward, created_at FROM signals ORDER BY created_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def _unrealized_pnl(row: sqlite3.Row, market: MarketDataService | None) -> float | None:
    if market is None:
        return None
    try:
        price = market.get_quote(row["symbol"]).price
    except Exception:
        return None
    return position_pnl(Action(row["action"]), row["units"], row["entry_price"], price)


def load_open_positions(
    conn: sqlite3.Connection, market: MarketDataService | None
) -> pd.DataFrame:
    """Open positions with unrealized P&L (`None` per row if a live quote
    isn't available — e.g. `market` is `None` or the quote fetch fails).
    """
    rows = conn.execute(
        "SELECT signal_id, symbol, action, units, entry_price, stop_loss, "
        "take_profit, risk_gbp, opened_at FROM positions WHERE closed_at IS NULL"
    ).fetchall()
    records = []
    for row in rows:
        record = dict(row)
        record["unrealized_pnl_gbp"] = _unrealized_pnl(row, market)
        records.append(record)
    return pd.DataFrame(records)


def equity_curve(conn: sqlite3.Connection, initial_bankroll: float) -> pd.DataFrame:
    """Cumulative realized equity after each position close, starting from
    the initial bankroll. Purely realized (no live prices), so this is
    exactly reproducible from the ledger alone.
    """
    rows = conn.execute(
        "SELECT closed_at, realized_pnl_gbp FROM positions "
        "WHERE closed_at IS NOT NULL ORDER BY closed_at"
    ).fetchall()
    equity = initial_bankroll
    points = [{"closed_at": None, "equity_gbp": equity}]
    for row in rows:
        equity += row["realized_pnl_gbp"]
        closed_at = datetime.fromisoformat(row["closed_at"])
        points.append({"closed_at": closed_at, "equity_gbp": equity})
    return pd.DataFrame(points)


def _build_market_safe(settings: Settings) -> MarketDataService | None:
    """Best-effort market service: the dashboard should still render (minus
    unrealized P&L and the weekly review) if credentials are unset/invalid.
    """
    from alphatrader.factories import build_market

    try:
        return build_market(settings)
    except Exception:
        return None


def main() -> None:
    import streamlit as st

    settings = get_settings()
    risk_cfg = load_risk_config(str(settings.risk_config_path))
    market = _build_market_safe(settings)

    st.set_page_config(page_title="AlphaTrader HITL", layout="wide")
    st.title("AlphaTrader HITL — read-only dashboard")
    st.caption(
        "Signal-only. This page never fills, skips, closes, halts, or resumes "
        "anything — every confirmation happens in Telegram."
    )

    with get_connection(settings.db_path) as conn:
        agent_state = lifecycle.get_agent_state(conn)
        balance = lifecycle.current_balance_gbp(conn, risk_cfg.initial_bankroll)
        positions_df = load_open_positions(conn, market)
        signals_df = load_open_signals(conn)
        recent_df = load_recent_signals(conn)
        equity_df = equity_curve(conn, risk_cfg.initial_bankroll)
        weekly_text = build_weekly_review(conn, market) if market is not None else None

    unrealized_total = (
        positions_df["unrealized_pnl_gbp"].dropna().sum() if not positions_df.empty else 0.0
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("Agent state", agent_state.value)
    col2.metric("Realized balance", f"£{balance:.2f}")
    col3.metric("Unrealized P&L", f"£{unrealized_total:.2f}")

    st.subheader("Pending signal cards")
    if signals_df.empty:
        st.write("No pending signals.")
    else:
        st.dataframe(signals_df, use_container_width=True)

    st.subheader("Open positions")
    if positions_df.empty:
        st.write("No open positions.")
    else:
        st.dataframe(positions_df, use_container_width=True)

    st.subheader("Equity curve (realized, GBP)")
    if len(equity_df) > 1:
        st.line_chart(equity_df.set_index("closed_at")["equity_gbp"])
    else:
        st.write("Not enough closed positions yet for an equity curve.")

    st.subheader("Recent signals")
    if recent_df.empty:
        st.write("No signals yet.")
    else:
        st.dataframe(recent_df, use_container_width=True)

    st.subheader("Weekly skipped-vs-taken review")
    if weekly_text is not None:
        st.text(weekly_text)
    else:
        st.write(
            "Market data unavailable (check ALPACA_KEY_ID/ALPACA_SECRET) — "
            "the weekly review needs live candle history."
        )


if __name__ == "__main__":
    main()
