"""Scan pipeline: market data -> LLM analyst -> risk engine -> ranked signal cards.

Persistence and lifecycle (expiry, fills, closes) are out of scope here; see
`signals/lifecycle.py`. This module is pure orchestration plus the ranking
heuristic borrowed from plan.md §8: only `risk/engine.py`'s accepted
verdicts ever become cards, and every number on a card traces back to it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from alphatrader.agent.analyst import propose
from alphatrader.data.indicators import ema
from alphatrader.data.market import MarketDataService
from alphatrader.llm.provider import LLMProvider
from alphatrader.models import Action, AgentStateName, Candle, RiskVerdict, TradeProposal
from alphatrader.risk.engine import RiskConfig, evaluate
from alphatrader.signals.cards import SignalCard

_RR_NORMALIZATION_CAP = 5.0  # R/R at or above this normalizes to 1.0 for ranking


@dataclass(frozen=True)
class ScanCandidate:
    symbol: str
    instrument_class: str
    proposal: TradeProposal
    verdict: RiskVerdict
    score: float


def _trend_alignment(candles: list[Candle], action: Action) -> float:
    """1.0 if the EMA20/50 trend agrees with the proposed direction, 0.0 if it
    opposes it, 0.5 if there isn't enough data to tell.
    """
    closes = [c.close for c in candles]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    if ema20[-1] is None or ema50[-1] is None:
        return 0.5
    uptrend = ema20[-1] > ema50[-1]
    if action == Action.BUY:
        return 1.0 if uptrend else 0.0
    if action == Action.SELL:
        return 0.0 if uptrend else 1.0
    return 0.5


def _score(verdict: RiskVerdict, proposal: TradeProposal, trend_alignment: float,
           weights: dict) -> float:
    rr_normalized = min(verdict.risk_reward / _RR_NORMALIZATION_CAP, 1.0)
    return (
        weights.get("rr_normalized", 0.4) * rr_normalized
        + weights.get("confidence", 0.3) * proposal.confidence
        + weights.get("trend_alignment", 0.3) * trend_alignment
    )


def scan(
    symbols: list[str],
    market: MarketDataService,
    provider: LLMProvider,
    risk_cfg: RiskConfig,
    balance_gbp: float,
    open_positions_count: int,
    agent_state: AgentStateName,
) -> list[SignalCard]:
    """Scan `symbols`, proposing and risk-evaluating each, and return the
    top `risk_cfg.max_cards_per_scan` accepted setups as ranked SignalCards.
    """
    candidates: list[ScanCandidate] = []
    for symbol in symbols:
        info = market.symbol_info(symbol)
        candles = market.get_candles(symbol)
        proposal = propose(provider, symbol, candles)
        verdict = evaluate(
            proposal,
            risk_cfg,
            balance_gbp=balance_gbp,
            typical_spread_bps=info.typical_spread_bps,
            open_positions_count=open_positions_count,
            agent_state=agent_state,
        )
        if not verdict.accepted:
            continue
        trend = _trend_alignment(candles, proposal.action)
        score = _score(verdict, proposal, trend, risk_cfg.ranking_weights)
        candidates.append(
            ScanCandidate(
                symbol=symbol,
                instrument_class=info.instrument_class,
                proposal=proposal,
                verdict=verdict,
                score=score,
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    top = candidates[: risk_cfg.max_cards_per_scan]

    expires_at = datetime.now(UTC) + timedelta(hours=risk_cfg.default_expiry_hours)
    cards: list[SignalCard] = []
    for idx, candidate in enumerate(top, start=1):
        proposal, verdict = candidate.proposal, candidate.verdict
        risk_pct = verdict.risk_gbp / balance_gbp * 100.0 if balance_gbp else 0.0
        cards.append(
            SignalCard(
                signal_id=idx,
                symbol=candidate.symbol,
                action=proposal.action.value,
                entry=proposal.entry,
                stop_loss=proposal.stop_loss,
                take_profit=proposal.take_profit,
                units=verdict.units,
                risk_gbp=verdict.risk_gbp,
                amount_gbp=verdict.amount_gbp,
                risk_reward=verdict.risk_reward,
                spread_multiple=verdict.spread_multiple or 0.0,
                rationale=proposal.rationale,
                expires_at=expires_at,
                initial_bankroll=risk_cfg.initial_bankroll,
                risk_pct=risk_pct,
                confidence=proposal.confidence,
                instrument_class=candidate.instrument_class,
            )
        )
    return cards
