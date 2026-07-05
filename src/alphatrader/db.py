"""SQLite schema and connection management.

Tables: signals, fills, positions, pnl_daily, agent_state, audit_log.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    entry REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    units REAL NOT NULL,
    risk_gbp REAL NOT NULL,
    amount_gbp REAL NOT NULL,
    risk_reward REAL NOT NULL,
    confidence REAL NOT NULL,
    rationale TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    expires_at TEXT,
    fill_price REAL,
    close_price REAL,
    close_reason TEXT
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL REFERENCES signals(id),
    fill_price REAL NOT NULL,
    filled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL REFERENCES signals(id),
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    units REAL NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    risk_gbp REAL NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    close_price REAL,
    realized_pnl_gbp REAL
);

CREATE TABLE IF NOT EXISTS pnl_daily (
    date TEXT PRIMARY KEY,
    realized_gbp REAL NOT NULL,
    unrealized_gbp REAL NOT NULL,
    balance_gbp REAL NOT NULL,
    drawdown_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL DEFAULT 'ACTIVE',
    reason TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO agent_state (id, state, reason, updated_at) "
            "VALUES (1, 'ACTIVE', '', datetime('now'))"
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_connection(db_path: str | Path):
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
