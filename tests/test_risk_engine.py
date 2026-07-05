from pathlib import Path

import pytest

from alphatrader.models import Action, AgentStateName, TradeProposal
from alphatrader.risk.engine import (
    ABS_MAX_POSITIONS,
    ABS_MAX_RISK_PCT,
    ABS_MIN_RR,
    EXECUTION,
    RiskConfigError,
    evaluate,
    load_risk_config,
    validate_risk_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_execution_is_always_none():
    assert EXECUTION is None


def test_loads_real_repo_risk_config():
    cfg = load_risk_config(str(REPO_ROOT / "config" / "risk.yaml"))
    assert cfg.max_risk_per_trade_percent <= ABS_MAX_RISK_PCT
    assert cfg.min_risk_reward_ratio >= ABS_MIN_RR
    assert cfg.max_concurrent_positions <= ABS_MAX_POSITIONS


def test_config_looser_than_ceiling_raises_risk_pct(risk_config):
    bad = risk_config.__class__(**{**risk_config.__dict__, "max_risk_per_trade_percent": 5.0})
    with pytest.raises(RiskConfigError):
        validate_risk_config(bad)


def test_config_looser_than_ceiling_raises_rr(risk_config):
    bad = risk_config.__class__(**{**risk_config.__dict__, "min_risk_reward_ratio": 1.0})
    with pytest.raises(RiskConfigError):
        validate_risk_config(bad)


def test_config_looser_than_ceiling_raises_positions(risk_config):
    bad = risk_config.__class__(**{**risk_config.__dict__, "max_concurrent_positions": 10})
    with pytest.raises(RiskConfigError):
        validate_risk_config(bad)


def _valid_buy_proposal() -> TradeProposal:
    return TradeProposal(
        symbol="AAPL",
        action=Action.BUY,
        entry=100.0,
        stop_loss=99.0,   # 1% stop, well within 2% ceiling
        take_profit=103.0,  # R/R = 3.0
        confidence=0.7,
        rationale="test",
    )


def test_accepts_valid_proposal(risk_config):
    verdict = evaluate(
        _valid_buy_proposal(),
        risk_config,
        balance_gbp=1000.0,
        typical_spread_bps=3,
        open_positions_count=0,
        agent_state=AgentStateName.ACTIVE,
    )
    assert verdict.accepted
    assert verdict.risk_gbp <= 10.0 + 1e-9
    assert verdict.risk_reward >= risk_config.min_risk_reward_ratio


def test_rejects_when_agent_halted(risk_config):
    verdict = evaluate(
        _valid_buy_proposal(),
        risk_config,
        balance_gbp=1000.0,
        typical_spread_bps=3,
        open_positions_count=0,
        agent_state=AgentStateName.HALTED_DAILY,
    )
    assert not verdict.accepted
    assert "halted" in verdict.reason


def test_rejects_hold(risk_config):
    proposal = _valid_buy_proposal()
    proposal.action = Action.HOLD
    verdict = evaluate(
        proposal, risk_config, 1000.0, 3, 0, AgentStateName.ACTIVE
    )
    assert not verdict.accepted
    assert verdict.reason == "proposal is hold"


def test_rejects_stop_beyond_hard_limit(risk_config):
    proposal = _valid_buy_proposal()
    proposal.stop_loss = 95.0  # 5% stop, exceeds 2% ceiling
    verdict = evaluate(proposal, risk_config, 1000.0, 3, 0, AgentStateName.ACTIVE)
    assert not verdict.accepted
    assert "hard_stop_loss_percent" in verdict.reason


def test_rejects_low_risk_reward(risk_config):
    proposal = _valid_buy_proposal()
    proposal.take_profit = 100.5  # R/R < 2
    verdict = evaluate(proposal, risk_config, 1000.0, 3, 0, AgentStateName.ACTIVE)
    assert not verdict.accepted
    assert "risk/reward" in verdict.reason


def test_rejects_max_positions_reached(risk_config):
    verdict = evaluate(
        _valid_buy_proposal(), risk_config, 1000.0, 3,
        open_positions_count=risk_config.max_concurrent_positions,
        agent_state=AgentStateName.ACTIVE,
    )
    assert not verdict.accepted
    assert "max_concurrent_positions" in verdict.reason


def test_rejects_when_preservation_state(risk_config):
    # The £940 floor is enforced at the ledger/breaker level (risk/state.py);
    # evaluate() only needs to respect agent_state == PRESERVATION.
    verdict = evaluate(
        _valid_buy_proposal(), risk_config, balance_gbp=900.0, typical_spread_bps=3,
        open_positions_count=0, agent_state=AgentStateName.PRESERVATION,
    )
    assert not verdict.accepted
    assert "halted" in verdict.reason
