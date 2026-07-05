"""Renders the eToro-ready Signal Card (plan.md §4.3).

Every number on the card comes straight from a `RiskVerdict` produced by
`risk/engine.py` — never from the LLM. This module only formats text.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

_ENTRY_SLIPPAGE_PCT = 0.3  # skip-if-past buffer shown to the user, in percent of entry


@dataclass(frozen=True)
class SignalCard:
    signal_id: int
    symbol: str
    action: str  # "buy" or "sell"
    entry: float
    stop_loss: float
    take_profit: float
    units: float
    risk_gbp: float
    amount_gbp: float
    risk_reward: float
    spread_multiple: float
    rationale: str
    expires_at: datetime
    initial_bankroll: float
    risk_pct: float
    confidence: float = 0.0
    instrument_class: str = "stock"


def render(card: SignalCard) -> str:
    emoji = "🟢" if card.action == "buy" else "🔴"
    stop_pct = (card.stop_loss - card.entry) / card.entry * 100.0
    if card.action == "buy":
        limit_skip = card.entry * (1 + _ENTRY_SLIPPAGE_PCT / 100.0)
    else:
        limit_skip = card.entry * (1 - _ENTRY_SLIPPAGE_PCT / 100.0)

    lines = [
        f"{emoji} SIGNAL #{card.signal_id} — {card.action.upper()} {card.symbol}"
        f"          (expires {card.expires_at.strftime('%H:%M')} UTC)",
        f"Entry ......... {card.entry:.2f}   "
        f"(limit; skip if price is past {limit_skip:.2f})",
        f"Stop-loss ..... {card.stop_loss:.2f}   ({stop_pct:+.2f}%)",
        f"Take-profit ... {card.take_profit:.2f}   (R/R {card.risk_reward:.1f})",
        f"eToro amount .. £{card.amount_gbp:.0f}  "
        f"(= {card.units:.2f} units — set SL/TP by RATE, not %)",
        f"Planned risk .. £{card.risk_gbp:.2f} ({card.risk_pct:.1f}% of "
        f"£{card.initial_bankroll:.0f})   "
        f"Spread buffer: ok (stop = {card.spread_multiple:.1f}\u00d7 spread)",
        f"Why: {card.rationale}",
    ]
    if card.instrument_class in ("stock", "etf"):
        lines.append("Note: set leverage to X1 (no leverage) for this instrument.")
    lines.append("\u26a0 Signal, not advice — you decide.  [I placed it] [Skip]")
    return "\n".join(lines)
