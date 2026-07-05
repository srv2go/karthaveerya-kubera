"""AgentState machine and circuit breakers.

Breakers act on the ledger (realized + unrealized P&L), never on the LLM or
on any broker connection (there is none). Once tripped, no new signals are
issued until the relevant reset condition or a manual /resume.
"""
from __future__ import annotations

from dataclasses import dataclass

from alphatrader.models import AgentStateName
from alphatrader.risk.engine import RiskConfig


@dataclass
class BreakerResult:
    state: AgentStateName
    reason: str = ""


def evaluate_breakers(
    cfg: RiskConfig,
    equity_gbp: float,
    day_start_balance_gbp: float,
    week_start_balance_gbp: float,
) -> BreakerResult:
    """Determine the AgentState given current equity and period-start balances.

    Precedence: cash preservation floor > weekly loss limit > daily loss limit > active.
    """
    if equity_gbp <= cfg.cash_preservation_floor_gbp:
        return BreakerResult(
            AgentStateName.PRESERVATION,
            f"equity £{equity_gbp:.2f} at/below cash preservation floor "
            f"£{cfg.cash_preservation_floor_gbp:.2f}",
        )

    weekly_loss = week_start_balance_gbp - equity_gbp
    weekly_limit = week_start_balance_gbp * cfg.weekly_loss_limit_percent / 100.0
    if weekly_loss >= weekly_limit > 0:
        return BreakerResult(
            AgentStateName.HALTED_WEEKLY,
            f"weekly loss £{weekly_loss:.2f} >= limit £{weekly_limit:.2f}",
        )

    daily_loss = day_start_balance_gbp - equity_gbp
    daily_limit = day_start_balance_gbp * cfg.daily_loss_limit_percent / 100.0
    if daily_loss >= daily_limit > 0:
        return BreakerResult(
            AgentStateName.HALTED_DAILY,
            f"daily loss £{daily_loss:.2f} >= limit £{daily_limit:.2f}",
        )

    return BreakerResult(AgentStateName.ACTIVE, "")


def can_issue_signals(state: AgentStateName) -> bool:
    return state == AgentStateName.ACTIVE
