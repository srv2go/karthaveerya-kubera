from alphatrader.risk.engine import spread_floor_ok


def test_stop_wide_enough_passes():
    # entry 100, spread 3bps = 0.03; stop distance 1.0 -> ~33x spread
    ok, multiple = spread_floor_ok(
        entry=100.0, stop_loss=99.0, typical_spread_bps=3, min_multiple=3.0
    )
    assert ok
    assert multiple > 3.0


def test_stop_too_tight_for_spread_rejected():
    # entry 100, spread 25bps (crypto-like) = 0.25; stop distance 0.5 -> 2x spread, below floor
    ok, multiple = spread_floor_ok(
        entry=100.0, stop_loss=99.5, typical_spread_bps=25, min_multiple=3.0
    )
    assert not ok
    assert multiple < 3.0


def test_just_above_multiple_passes():
    # spread 10bps => 0.10 abs; stop distance slightly above 3x = 0.31
    ok, multiple = spread_floor_ok(
        entry=100.0, stop_loss=99.69, typical_spread_bps=10, min_multiple=3.0
    )
    assert ok
    assert multiple > 3.0
