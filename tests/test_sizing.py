from hypothesis import given
from hypothesis import strategies as st

from alphatrader.risk.engine import size_position


@given(
    risk_amount_gbp=st.floats(min_value=0.01, max_value=10.0, allow_nan=False),
    entry=st.floats(min_value=0.01, max_value=100_000.0, allow_nan=False),
    stop_distance=st.floats(min_value=0.0001, max_value=1_000.0, allow_nan=False),
)
def test_risk_never_exceeds_risk_amount(risk_amount_gbp, entry, stop_distance):
    stop_loss = entry - stop_distance
    units, risk_gbp = size_position(risk_amount_gbp, entry, stop_loss)
    assert units >= 0
    # Rounding is always DOWN, so realized risk must never exceed the cap.
    assert risk_gbp <= risk_amount_gbp + 1e-9


@given(
    risk_amount_gbp=st.floats(min_value=0.01, max_value=10.0, allow_nan=False),
)
def test_zero_stop_distance_yields_zero_units(risk_amount_gbp):
    units, risk_gbp = size_position(risk_amount_gbp, entry=100.0, stop_loss=100.0)
    assert units == 0.0
    assert risk_gbp == 0.0
