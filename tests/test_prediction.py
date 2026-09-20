import pytest

from config import PREDICTION_BIG_THRESHOLD, PREDICTION_FLAT_THRESHOLD
from app.engine.prediction import bucket_side, classify_realized_move, predict_movement
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


def test_small_up_and_down():
    assert predict_movement(_uniform_signals("bullish", 30)).bucket == "small_up"
    assert predict_movement(_uniform_signals("bearish", 30)).bucket == "small_down"


def test_big_up_and_down():
    assert predict_movement(_uniform_signals("bullish", 70)).bucket == "big_up"
    assert predict_movement(_uniform_signals("bearish", 70)).bucket == "big_down"


def test_confidence_tracks_net_score_magnitude_for_directional_buckets():
    pred = predict_movement(_uniform_signals("bullish", 42))
    assert pred.net_score == pytest.approx(42.0)
    assert pred.confidence == pytest.approx(42.0)


def test_boundary_at_flat_threshold_is_directional_not_flat():
    pred = predict_movement(_uniform_signals("bullish", PREDICTION_FLAT_THRESHOLD))
    assert pred.bucket == "small_up"


def test_boundary_at_big_threshold_is_big():
    pred = predict_movement(_uniform_signals("bullish", PREDICTION_BIG_THRESHOLD))
    assert pred.bucket == "big_up"


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
