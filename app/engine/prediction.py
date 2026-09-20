"""Always-on 5-bucket movement prediction: big down / small down / flat /
small up / big up, each with a confidence score. Distinct from the trade
suggestion (which is gated by CONFIDENCE_THRESHOLD and can legitimately say
nothing) -- this always outputs one of the 5 buckets, built from the exact
same weighted signal consensus so it stays consistent with the trade logic
rather than being a second, disconnected model.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import (
    PREDICTION_BIG_MOVE_PCT,
    PREDICTION_BIG_THRESHOLD,
    PREDICTION_FLAT_MOVE_PCT,
    PREDICTION_FLAT_THRESHOLD,
)
from app.engine.confidence import weighted_direction_scores
from app.engine.signals import SignalResult

BUCKETS = ["big_down", "small_down", "flat", "small_up", "big_up"]

LABELS = {
    "big_down": "Big Down",
    "small_down": "Small Down",
    "flat": "Flat",
    "small_up": "Small Up",
    "big_up": "Big Up",
}


@dataclass
class MovementPrediction:
    bucket: str
    confidence: float  # 0-100, confidence in the chosen bucket specifically
    net_score: float  # -100..100, signed signal consensus (bullish - bearish)


def bucket_side(bucket: str) -> str:
    """'up' | 'down' | 'flat' -- for a softer "was the direction at least
    right" accuracy check, distinct from an exact bucket match."""
    if bucket == "flat":
        return "flat"
    return bucket.split("_")[1]


def predict_movement(signals: list[SignalResult]) -> MovementPrediction:
    bullish, bearish = weighted_direction_scores(signals)
    net = bullish - bearish
    abs_net = abs(net)

    if abs_net < PREDICTION_FLAT_THRESHOLD:
        bucket = "flat"
        # Closer to 0 -> more confident it's flat; right at the boundary -> 0%.
        confidence = max(0.0, 100 - abs_net * (100 / PREDICTION_FLAT_THRESHOLD))
    else:
        direction = "up" if net > 0 else "down"
        size = "big" if abs_net >= PREDICTION_BIG_THRESHOLD else "small"
        bucket = f"{size}_{direction}"
        confidence = min(100.0, abs_net)

    return MovementPrediction(bucket=bucket, confidence=round(confidence, 1), net_score=round(net, 1))


def classify_realized_move(pct_change: float) -> str:
    """Buckets an actual observed % price change using its own thresholds
    (distinct from the signal-score thresholds above, since this compares
    real price movement, not model consensus)."""
    abs_pct = abs(pct_change)
    if abs_pct < PREDICTION_FLAT_MOVE_PCT:
        return "flat"
    direction = "up" if pct_change > 0 else "down"
    size = "big" if abs_pct >= PREDICTION_BIG_MOVE_PCT else "small"
    return f"{size}_{direction}"
