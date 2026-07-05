"""Deterministic risk engine.

This module is the ONLY place that decides whether a proposal becomes a
signal and at what size. The LLM never sizes anything. Nothing in this
module ever places, modifies, or cancels a real order.

Compiled-in ceilings below are hard limits: even if config/risk.yaml is
looser, the application must refuse to start (see `validate_risk_config`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

import yaml

from alphatrader.models import Action, AgentStateName, RiskVerdict, TradeProposal

# --- Compiled-in ceilings (not configurable) ---------------------------------
ABS_MAX_RISK_PCT = 1.0
ABS_MIN_RR = 2.0
ABS_MAX_POSITIONS = 5
EXECUTION = None  # There is no execution client. This is intentional and permanent.


class RiskConfigError(ValueError):
    """Raised when config/risk.yaml is looser than the compiled-in ceilings."""


@dataclass(frozen=True)
class RiskConfig:
    initial_bankroll: float
    currency: str
    max_risk_per_trade_percent: float
    hard_stop_loss_percent: float
    daily_loss_limit_percent: float
    weekly_loss_limit_percent: float
    min_risk_reward_ratio: float
    max_concurrent_positions: int
    cash_preservation_floor_gbp: float
    min_stop_distance_spread_multiple: float
    position_sizing_method: str
    default_expiry_hours: int
    daily_scan_time_utc: str
    weekly_profit_target_gbp: float
    ranking_weights: dict
    max_cards_per_scan: int


def load_risk_config(path: str) -> RiskConfig:
    with open(path) as fh:
        raw = yaml.safe_load(fh)
    rm = raw["risk_management"]
    sig = raw.get("signals", {})
    cfg = RiskConfig(
        initial_bankroll=float(rm["initial_bankroll"]),
        currency=rm.get("currency", "GBP"),
        max_risk_per_trade_percent=float(rm["max_risk_per_trade_percent"]),
        hard_stop_loss_percent=float(rm["hard_stop_loss_percent"]),
        daily_loss_limit_percent=float(rm["daily_loss_limit_percent"]),
        weekly_loss_limit_percent=float(rm["weekly_loss_limit_percent"]),
        min_risk_reward_ratio=float(rm["min_risk_reward_ratio"]),
        max_concurrent_positions=int(rm["max_concurrent_positions"]),
        cash_preservation_floor_gbp=float(rm["cash_preservation_floor_gbp"]),
        min_stop_distance_spread_multiple=float(rm["min_stop_distance_spread_multiple"]),
        position_sizing_method=rm.get("position_sizing_method", "fixed_fractional"),
        default_expiry_hours=int(sig.get("default_expiry_hours", 4)),
        daily_scan_time_utc=str(sig.get("daily_scan_time_utc", "07:30")),
        weekly_profit_target_gbp=float(sig.get("weekly_profit_target_gbp", 0.0)),
        ranking_weights=sig.get(
            "ranking_weights",
            {"rr_normalized": 0.4, "confidence": 0.3, "trend_alignment": 0.3},
        ),
        max_cards_per_scan=int(sig.get("max_cards_per_scan", 3)),
    )
    validate_risk_config(cfg)
    return cfg


def validate_risk_config(cfg: RiskConfig) -> None:
    """Abort-worthy check: config must never be looser than compiled-in ceilings."""
    if cfg.max_risk_per_trade_percent > ABS_MAX_RISK_PCT:
        raise RiskConfigError(
            f"max_risk_per_trade_percent={cfg.max_risk_per_trade_percent} exceeds "
            f"compiled-in ceiling ABS_MAX_RISK_PCT={ABS_MAX_RISK_PCT}"
        )
    if cfg.min_risk_reward_ratio < ABS_MIN_RR:
        raise RiskConfigError(
            f"min_risk_reward_ratio={cfg.min_risk_reward_ratio} is below "
            f"compiled-in ceiling ABS_MIN_RR={ABS_MIN_RR}"
        )
    if cfg.max_concurrent_positions > ABS_MAX_POSITIONS:
        raise RiskConfigError(
            f"max_concurrent_positions={cfg.max_concurrent_positions} exceeds "
            f"compiled-in ceiling ABS_MAX_POSITIONS={ABS_MAX_POSITIONS}"
        )
    if EXECUTION is not None:
        # This branch must be unreachable. Kept as a defense-in-depth invariant.
        raise RiskConfigError("EXECUTION must always be None: this system never trades.")


def spread_floor_ok(
    entry: float, stop_loss: float, typical_spread_bps: float, min_multiple: float
) -> tuple[bool, float]:
    """Reject setups whose stop distance is < min_multiple x typical spread."""
    stop_distance = abs(entry - stop_loss)
    spread_abs = entry * (typical_spread_bps / 10_000.0)
    if spread_abs <= 0:
        return True, math.inf
    multiple = stop_distance / spread_abs
    return multiple >= min_multiple, multiple


_UNIT_QUANTUM = Decimal("0.000001")  # smallest unit increment; sizing always rounds DOWN


def size_position(risk_amount_gbp: float, entry: float, stop_loss: float) -> tuple[float, float]:
    """Fixed-fractional sizing. Rounds DOWN so realized risk never exceeds
    risk_amount_gbp, even after floating-point/decimal rounding.

    Returns (units, risk_gbp).
    """
    stop_distance = Decimal(str(abs(entry - stop_loss)))
    if stop_distance <= 0:
        return 0.0, 0.0
    risk_amount = Decimal(str(risk_amount_gbp))
    raw_units = risk_amount / stop_distance
    units = raw_units.quantize(_UNIT_QUANTUM, rounding=ROUND_DOWN)
    risk_gbp = units * stop_distance
    return float(units), float(risk_gbp)


def evaluate(
    proposal: TradeProposal,
    cfg: RiskConfig,
    balance_gbp: float,
    typical_spread_bps: float,
    open_positions_count: int,
    agent_state: AgentStateName,
) -> RiskVerdict:
    """Validate a TradeProposal and, if accepted, size it. This is the ONLY
    function that turns an LLM proposal into risk numbers a signal card can
    show. Every rejection carries a human-readable reason.
    """
    if agent_state != AgentStateName.ACTIVE:
        return RiskVerdict(accepted=False, reason=f"agent halted: {agent_state.value}")

    if proposal.action == Action.HOLD:
        return RiskVerdict(accepted=False, reason="proposal is hold")

    if proposal.entry is None or proposal.stop_loss is None or proposal.take_profit is None:
        return RiskVerdict(accepted=False, reason="incomplete proposal: missing entry/stop/target")

    entry, stop_loss, take_profit = proposal.entry, proposal.stop_loss, proposal.take_profit

    if entry <= 0:
        return RiskVerdict(accepted=False, reason="entry must be positive")

    stop_distance = abs(entry - stop_loss)
    if stop_distance <= 0:
        return RiskVerdict(accepted=False, reason="stop_loss equals entry")

    stop_pct = stop_distance / entry * 100.0
    if stop_pct > cfg.hard_stop_loss_percent:
        return RiskVerdict(
            accepted=False,
            reason=(
                f"stop distance {stop_pct:.2f}% exceeds hard_stop_loss_percent "
                f"{cfg.hard_stop_loss_percent}%"
            ),
        )

    reward_distance = abs(take_profit - entry)
    risk_reward = reward_distance / stop_distance
    if risk_reward < cfg.min_risk_reward_ratio:
        return RiskVerdict(
            accepted=False,
            reason=f"risk/reward {risk_reward:.2f} below minimum {cfg.min_risk_reward_ratio}",
        )

    spread_ok, spread_multiple = spread_floor_ok(
        entry, stop_loss, typical_spread_bps, cfg.min_stop_distance_spread_multiple
    )
    if not spread_ok:
        return RiskVerdict(
            accepted=False,
            reason=(
                f"stop distance is only {spread_multiple:.2f}x typical spread, "
                f"below minimum {cfg.min_stop_distance_spread_multiple}x"
            ),
            spread_multiple=spread_multiple,
        )

    if open_positions_count >= cfg.max_concurrent_positions:
        return RiskVerdict(
            accepted=False,
            reason=f"max_concurrent_positions ({cfg.max_concurrent_positions}) reached",
        )

    # Note: the £940 cash-preservation floor is a ledger-level circuit breaker
    # (see risk/state.py::evaluate_breakers), not a per-trade notional check.
    # When the floor is breached, agent_state becomes PRESERVATION and is
    # rejected by the "agent halted" check above — so it is never reached here.
    risk_amount_gbp = balance_gbp * cfg.max_risk_per_trade_percent / 100.0
    units, risk_gbp = size_position(risk_amount_gbp, entry, stop_loss)
    if units <= 0:
        return RiskVerdict(accepted=False, reason="sized to zero units")

    amount_gbp = units * entry

    return RiskVerdict(
        accepted=True,
        reason="ok",
        units=units,
        risk_gbp=risk_gbp,
        amount_gbp=amount_gbp,
        risk_reward=risk_reward,
        spread_multiple=spread_multiple,
    )
