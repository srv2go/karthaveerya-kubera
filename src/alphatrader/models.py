"""Core domain models shared across the application.

These are plain data structures. Sizing/validation decisions are made by
`risk.engine`, never here.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class InstrumentClass(str, Enum):
    STOCK = "stock"
    ETF = "etf"
    CRYPTO = "crypto"


class Candle(BaseModel):
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class Quote(BaseModel):
    symbol: str
    price: float
    timestamp: datetime
    bid: float | None = None
    ask: float | None = None


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class TradeProposal(BaseModel):
    """Raw output from the LLM analyst. Never used for sizing directly."""

    symbol: str
    action: Action
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    confidence: float = 0.0
    rationale: str = ""


class RiskVerdict(BaseModel):
    """Result of RiskEngine.validate()/size() for a single proposal."""

    accepted: bool
    reason: str = ""
    units: float = 0.0
    risk_gbp: float = 0.0
    amount_gbp: float = 0.0
    risk_reward: float = 0.0
    spread_multiple: float | None = None


class SignalStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    SKIPPED = "skipped"
    EXPIRED = "expired"
    CLOSED = "closed"


class Signal(BaseModel):
    id: int | None = None
    symbol: str
    action: Action
    entry: float
    stop_loss: float
    take_profit: float
    units: float
    risk_gbp: float
    amount_gbp: float
    risk_reward: float
    confidence: float
    rationale: str
    score: float = 0.0
    status: SignalStatus = SignalStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    fill_price: float | None = None
    close_price: float | None = None
    close_reason: str | None = None


class FillReport(BaseModel):
    signal_id: int
    fill_price: float
    filled_at: datetime = Field(default_factory=datetime.utcnow)


class Position(BaseModel):
    id: int | None = None
    signal_id: int
    symbol: str
    action: Action
    units: float
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_gbp: float
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: datetime | None = None
    close_price: float | None = None
    realized_pnl_gbp: float | None = None


class PnLSnapshot(BaseModel):
    date: datetime
    realized_gbp: float
    unrealized_gbp: float
    balance_gbp: float
    drawdown_pct: float


class AgentStateName(str, Enum):
    ACTIVE = "ACTIVE"
    HALTED_DAILY = "HALTED_DAILY"
    HALTED_WEEKLY = "HALTED_WEEKLY"
    PRESERVATION = "PRESERVATION"


class AgentState(BaseModel):
    state: AgentStateName = AgentStateName.ACTIVE
    reason: str = ""
    updated_at: datetime = Field(default_factory=datetime.utcnow)
