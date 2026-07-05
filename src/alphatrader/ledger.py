"""Virtual GBP ledger.

Tracks the paper bankroll: applies FillReports to open positions, books
realized P&L on close, computes mark-to-market unrealized P&L, and tracks
drawdown from the high-water mark. This module never talks to a broker —
it only reacts to the user's own /filled and /closed confirmations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from alphatrader.models import Action, Position


def position_pnl(action: Action, units: float, entry_price: float, close_price: float) -> float:
    """Direction-aware P&L for a single position. Shared with signals/lifecycle.py
    so DB-backed closes and the in-memory Ledger agree on the same math.
    """
    diff = close_price - entry_price
    if action == Action.SELL:
        diff = -diff
    return diff * units


@dataclass
class Ledger:
    balance_gbp: float
    realized_pnl_gbp: float = 0.0
    peak_equity_gbp: float | None = None
    open_positions: dict[int, Position] = field(default_factory=dict)
    _next_position_id: int = 1

    def __post_init__(self) -> None:
        if self.peak_equity_gbp is None:
            self.peak_equity_gbp = self.balance_gbp

    def open_position(self, signal_id: int, symbol: str, action: Action, units: float,
                       fill_price: float, stop_loss: float, take_profit: float,
                       risk_gbp: float) -> Position:
        position = Position(
            id=self._next_position_id,
            signal_id=signal_id,
            symbol=symbol,
            action=action,
            units=units,
            entry_price=fill_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_gbp=risk_gbp,
        )
        self.open_positions[position.id] = position
        self._next_position_id += 1
        return position

    def _position_pnl(self, position: Position, price: float) -> float:
        return position_pnl(position.action, position.units, position.entry_price, price)

    def close_position(self, position_id: int, close_price: float, reason: str = "") -> float:
        position = self.open_positions.pop(position_id)
        pnl = self._position_pnl(position, close_price)
        position.closed_at = datetime.now(UTC)
        position.close_price = close_price
        position.realized_pnl_gbp = pnl
        self.balance_gbp += pnl
        self.realized_pnl_gbp += pnl
        self.peak_equity_gbp = max(self.peak_equity_gbp, self.balance_gbp)
        return pnl

    def unrealized_pnl_gbp(self, prices: dict[str, float]) -> float:
        total = 0.0
        for position in self.open_positions.values():
            price = prices.get(position.symbol)
            if price is None:
                continue
            total += self._position_pnl(position, price)
        return total

    def equity_gbp(self, prices: dict[str, float]) -> float:
        return self.balance_gbp + self.unrealized_pnl_gbp(prices)

    def mark_to_market(self, prices: dict[str, float]) -> float:
        """Update the high-water mark using current equity; return equity."""
        equity = self.equity_gbp(prices)
        self.peak_equity_gbp = max(self.peak_equity_gbp, equity)
        return equity

    def drawdown_pct(self, prices: dict[str, float]) -> float:
        equity = self.equity_gbp(prices)
        if not self.peak_equity_gbp or self.peak_equity_gbp <= 0:
            return 0.0
        return max(0.0, (self.peak_equity_gbp - equity) / self.peak_equity_gbp * 100.0)
