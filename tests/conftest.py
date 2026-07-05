import pytest

from alphatrader.risk.engine import RiskConfig


@pytest.fixture
def risk_config() -> RiskConfig:
    return RiskConfig(
        initial_bankroll=1000.0,
        currency="GBP",
        max_risk_per_trade_percent=1.0,
        hard_stop_loss_percent=2.0,
        daily_loss_limit_percent=3.0,
        weekly_loss_limit_percent=6.0,
        min_risk_reward_ratio=2.0,
        max_concurrent_positions=5,
        cash_preservation_floor_gbp=940.0,
        min_stop_distance_spread_multiple=3.0,
        position_sizing_method="fixed_fractional",
        default_expiry_hours=4,
        daily_scan_time_utc="07:30",
        weekly_profit_target_gbp=150.0,
        ranking_weights={"rr_normalized": 0.4, "confidence": 0.3, "trend_alignment": 0.3},
        max_cards_per_scan=3,
    )
