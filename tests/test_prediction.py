import math

import pandas as pd
import pytest

from config import PREDICTION_BIG_MOVE_PCT, PREDICTION_FLAT_THRESHOLD, TZ
from app.data.market_data import OptionLeg, OptionsChain
from app.engine.prediction import (
    atr_expected_move_pct,
    bucket_side,
    classify_realized_move,
    iv_expected_move_pct,
    predict_movement,
)
from app.engine import indicators as ind
from app.engine.signals import SignalResult

_NAMES = ["trend", "momentum", "volume", "opening_range", "iv_skew"]


def _uniform_signals(direction: str, strength: float) -> list[SignalResult]:
    """All 5 signals sharing the same direction/strength. Since the signal
    weights sum to 100, this makes net_score equal `strength` exactly
    (signed by direction) -- a convenient way to hit precise net scores."""
    return [SignalResult(name, direction, strength, "test") for name in _NAMES]


def test_flat_when_no_consensus():
    pred = predict_movement(_uniform_signals("neutral", 0))
    assert pred.bucket == "flat"
    assert pred.confidence == pytest.approx(100.0)


def test_confidence_tracks_net_score_magnitude():
    pred = predict_movement(_uniform_signals("bullish", 42))
    assert pred.net_score == pytest.approx(42.0)
    assert pred.confidence == pytest.approx(42.0)


def test_boundary_at_flat_threshold_is_directional_not_flat():
    pred = predict_movement(_uniform_signals("bullish", PREDICTION_FLAT_THRESHOLD))
    assert pred.bucket == "small_up"  # no expected_move_pct given -> defaults to "small"


# --- Magnitude is decoupled from direction: it comes from expected_move_pct,
# NOT from how strong the directional signal consensus is. ---


def test_magnitude_defaults_to_small_without_any_volatility_evidence():
    """Very strong directional consensus (net_score=90) must NOT alone
    produce "big" -- magnitude requires actual volatility evidence."""
    pred = predict_movement(_uniform_signals("bullish", 90))
    assert pred.bucket == "small_up"
    assert pred.expected_move_pct is None


def test_big_bucket_when_expected_move_exceeds_threshold():
    pred = predict_movement(_uniform_signals("bullish", 30), expected_move_pct=PREDICTION_BIG_MOVE_PCT, magnitude_source="iv")
    assert pred.bucket == "big_up"
    assert pred.magnitude_source == "iv"


def test_small_bucket_despite_strong_direction_when_expected_move_is_low():
    """The core of the fix: a very strong directional score (90) with a LOW
    IV-implied expected move must still bucket as "small", not "big" -- if
    it didn't, magnitude would still just be reading direction confidence
    in disguise."""
    pred = predict_movement(_uniform_signals("bullish", 90), expected_move_pct=0.05, magnitude_source="iv")
    assert pred.bucket == "small_up"
    assert pred.confidence == pytest.approx(90.0)  # direction confidence is unaffected by magnitude


def test_big_bucket_with_only_moderate_direction_when_expected_move_is_high():
    """And the converse: a big expected move should be able to combine
    with only moderate directional confidence -- they're independent axes."""
    pred = predict_movement(_uniform_signals("bearish", 20), expected_move_pct=0.75, magnitude_source="iv")
    assert pred.bucket == "big_down"
    assert pred.confidence == pytest.approx(20.0)


def test_classify_realized_move_uses_same_threshold_as_prediction():
    assert classify_realized_move(PREDICTION_BIG_MOVE_PCT) == "big_up"


@pytest.mark.parametrize(
    "pct,expected",
    [
        (0.0, "flat"),
        (0.04, "flat"),
        (-0.04, "flat"),
        (0.06, "small_up"),
        (-0.06, "small_down"),
        (0.35, "big_up"),
        (-0.35, "big_down"),
    ],
)
def test_classify_realized_move(pct, expected):
    assert classify_realized_move(pct) == expected


def test_bucket_side():
    assert bucket_side("flat") == "flat"
    assert bucket_side("small_up") == "up"
    assert bucket_side("big_up") == "up"
    assert bucket_side("small_down") == "down"
    assert bucket_side("big_down") == "down"


# --- Magnitude evidence sources ---


def _leg(strike, iv):
    return OptionLeg(strike=strike, bid=1.0, ask=1.0, last=1.0, volume=100, open_interest=100, implied_vol=iv)


def test_iv_expected_move_pct_matches_formula():
    chain = OptionsChain(expiration="2026-01-06", underlying_price=500.0, calls=[_leg(500.0, 0.20)], puts=[_leg(500.0, 0.20)])
    horizon_years = 0.001
    result = iv_expected_move_pct(chain, 500.0, horizon_years)
    assert result == pytest.approx(0.20 * math.sqrt(horizon_years) * 100)


def test_iv_expected_move_pct_none_without_chain_or_spot():
    assert iv_expected_move_pct(None, 500.0, 0.001) is None
    chain = OptionsChain(expiration="2026-01-06", underlying_price=500.0, calls=[_leg(500.0, 0.20)], puts=[])
    assert iv_expected_move_pct(chain, None, 0.001) is None
    assert iv_expected_move_pct(chain, float("nan"), 0.001) is None


def test_atr_expected_move_pct_matches_formula():
    idx = pd.date_range("2026-01-06 09:30", periods=20, freq="1min", tz=TZ)
    bars = pd.DataFrame({"Open": 500.0, "High": 501.0, "Low": 499.0, "Close": 500.0, "Volume": 1000}, index=idx)
    atr_val = ind.atr(bars, 14).iloc[-1]
    result = atr_expected_move_pct(bars, 500.0, 30)
    assert result == pytest.approx((atr_val / 500.0) * math.sqrt(30) * 100)


def test_atr_expected_move_pct_none_with_too_few_bars():
    idx = pd.date_range("2026-01-06 09:30", periods=5, freq="1min", tz=TZ)
    bars = pd.DataFrame({"Open": 500.0, "High": 501.0, "Low": 499.0, "Close": 500.0, "Volume": 1000}, index=idx)
    assert atr_expected_move_pct(bars, 500.0, 30) is None
