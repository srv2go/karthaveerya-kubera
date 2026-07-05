"""Summary statistics for a BacktestResult: equity curve, drawdown, hit rate,
expectancy, and R-multiples. Pure computation only — no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

from alphatrader.backtest.engine import BacktestResult


@dataclass
class BacktestReport:
    total_trades: int
    wins: int
    losses: int
    hit_rate_pct: float
    expectancy_gbp: float
    avg_r_multiple: float
    max_drawdown_pct: float
    initial_balance_gbp: float
    final_balance_gbp: float
    total_return_pct: float
    halted: bool
    halt_count: int


def _max_drawdown_pct(equity_curve: list[tuple[object, float]]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0][1]
    max_dd = 0.0
    for _, equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            max_dd = max(max_dd, dd)
    return max_dd


def generate_report(result: BacktestResult) -> BacktestReport:
    trades = result.trades
    total_trades = len(trades)
    wins = sum(1 for t in trades if (t.pnl_gbp or 0.0) > 0)
    losses = sum(1 for t in trades if (t.pnl_gbp or 0.0) <= 0)
    hit_rate_pct = (wins / total_trades * 100.0) if total_trades else 0.0

    pnls = [t.pnl_gbp or 0.0 for t in trades]
    expectancy_gbp = sum(pnls) / total_trades if total_trades else 0.0

    r_multiples = [
        (t.pnl_gbp / t.risk_gbp) for t in trades if t.risk_gbp and t.pnl_gbp is not None
    ]
    avg_r_multiple = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0

    max_drawdown_pct = _max_drawdown_pct(result.equity_curve)

    total_return_pct = (
        (result.final_balance_gbp - result.initial_balance_gbp) / result.initial_balance_gbp * 100.0
        if result.initial_balance_gbp
        else 0.0
    )

    return BacktestReport(
        total_trades=total_trades,
        wins=wins,
        losses=losses,
        hit_rate_pct=hit_rate_pct,
        expectancy_gbp=expectancy_gbp,
        avg_r_multiple=avg_r_multiple,
        max_drawdown_pct=max_drawdown_pct,
        initial_balance_gbp=result.initial_balance_gbp,
        final_balance_gbp=result.final_balance_gbp,
        total_return_pct=total_return_pct,
        halted=bool(result.halts),
        halt_count=len(result.halts),
    )
