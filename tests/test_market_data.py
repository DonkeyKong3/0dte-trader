import math

import pandas as pd

from app.data.market_data import _safe_float, _safe_int, _to_legs


def test_safe_float_handles_nan():
    assert _safe_float(float("nan")) == 0.0
    assert _safe_float(float("nan"), default=1.5) == 1.5
    assert _safe_float(None) == 0.0
    assert _safe_float(3.2) == 3.2


def test_safe_int_handles_nan():
    assert _safe_int(float("nan")) == 0
    assert _safe_int(None) == 0
    assert _safe_int(42.0) == 42


def test_to_legs_survives_nan_volume_and_open_interest():
    """Regression test: Yahoo commonly reports NaN volume/openInterest for
    illiquid or newly listed contracts. `int(x or 0)` previously crashed on
    these because NaN is truthy in Python, so `NaN or 0` stays NaN and
    `int(NaN)` raises ValueError."""
    df = pd.DataFrame(
        {
            "strike": [500.0, 505.0],
            "bid": [1.2, float("nan")],
            "ask": [1.4, float("nan")],
            "lastPrice": [1.3, 0.05],
            "volume": [float("nan"), 0],
            "openInterest": [float("nan"), float("nan")],
            "impliedVolatility": [0.15, float("nan")],
        }
    )
    legs = _to_legs(df)
    assert len(legs) == 2
    assert legs[0].volume == 0
    assert legs[0].open_interest == 0
    assert legs[1].bid == 0.0
    assert legs[1].implied_vol is None
    assert not math.isnan(legs[0].volume)
