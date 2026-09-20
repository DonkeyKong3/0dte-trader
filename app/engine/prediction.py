"""Always-on 5-bucket movement prediction: big down / small down / flat /
small up / big up, each with a confidence score.

Direction and magnitude are deliberately independent:
  - DIRECTION (& confidence) comes from the same weighted signal consensus
    the trade-confidence gate uses (confidence.py) -- that's what the 5
    signals are actually good at estimating.
  - MAGNITUDE (small vs big) comes from the options market's own IV-implied
    expected move for the prediction horizon (spot * IV * sqrt(time)), with
    a realized-volatility (ATR) fallback when no chain is available.

An earlier version derived magnitude from the same net signal score as
direction, which made this prediction's confidence identical to the trade
card's confidence whenever the losing side of the signal vote was 0 (the
common case) -- effectively restating one number as two. Grounding
magnitude in independently-sourced volatility evidence (market-priced IV,
or actual recent price action as a fallback) instead of a second read of
the same 5 signals is what makes these genuinely different, not cosmetic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from config import PREDICTION_BIG_MOVE_PCT, PREDICTION_FLAT_MOVE_PCT, PREDICTION_FLAT_THRESHOLD
from app.data.market_data import OptionsChain
from app.engine import indicators as ind
from app.engine.confidence import weighted_direction_scores
from app.engine.signals import SignalResult
from app.engine.spreads import atm_iv

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
    confidence: float  # 0-100, confidence in the DIRECTION specifically
    net_score: float  # -100..100, signed signal consensus (bullish - bearish)
    expected_move_pct: float | None  # magnitude evidence: IV- or ATR-implied % move over the horizon
    magnitude_source: str  # "iv" | "atr" | "none" -- which evidence produced expected_move_pct


def bucket_side(bucket: str) -> str:
    """'up' | 'down' | 'flat' -- for a softer "was the direction at least
    right" accuracy check, distinct from an exact bucket match."""
    if bucket == "flat":
        return "flat"
    return bucket.split("_")[1]


def iv_expected_move_pct(chain: OptionsChain | None, spot: float | None, horizon_years: float) -> float | None:
    """1-sigma expected % move over `horizon_years`, priced by the options
    market right now (spot * ATM IV * sqrt(time)) -- the same "expected
    move" calculation options traders use to size ranges. Real money
    backing the number, independent of the technical signals."""
    if chain is None or not spot or spot != spot:
        return None
    iv = atm_iv(chain, spot)
    if not iv:
        return None
    return iv * math.sqrt(horizon_years) * 100


def atr_expected_move_pct(bars: pd.DataFrame | None, spot: float | None, horizon_minutes: float) -> float | None:
    """Fallback when there's no usable options chain (market closed, or a
    Tue/Thu gap day with no same-day SPY expiration): extrapolates today's
    realized 1-minute ATR out to the horizon under a random-walk (sqrt-
    time) assumption. Actual recent price action, still independent of the
    directional signal vote."""
    if bars is None or bars.empty or not spot or spot != spot or len(bars) < 14:
        return None
    atr = ind.atr(bars, 14).iloc[-1]
    if pd.isna(atr) or atr <= 0:
        return None
    atr_pct_per_bar = atr / spot
    return atr_pct_per_bar * math.sqrt(horizon_minutes) * 100


def predict_movement(
    signals: list[SignalResult],
    expected_move_pct: float | None = None,
    magnitude_source: str = "none",
) -> MovementPrediction:
    bullish, bearish = weighted_direction_scores(signals)
    net = bullish - bearish
    abs_net = abs(net)

    if abs_net < PREDICTION_FLAT_THRESHOLD:
        bucket = "flat"
        # Closer to 0 -> more confident it's flat; right at the boundary -> 0%.
        confidence = max(0.0, 100 - abs_net * (100 / PREDICTION_FLAT_THRESHOLD))
    else:
        direction = "up" if net > 0 else "down"
        size = "big" if (expected_move_pct or 0.0) >= PREDICTION_BIG_MOVE_PCT else "small"
        bucket = f"{size}_{direction}"
        confidence = min(100.0, abs_net)

    return MovementPrediction(
        bucket=bucket,
        confidence=round(confidence, 1),
        net_score=round(net, 1),
        expected_move_pct=round(expected_move_pct, 3) if expected_move_pct is not None else None,
        magnitude_source=magnitude_source,
    )


def classify_realized_move(pct_change: float) -> str:
    """Buckets an actual observed % price change using its own thresholds
    (the same PREDICTION_BIG_MOVE_PCT used above to classify the IV/ATR-
    implied expected move, so "big" means the same real-world thing on
    both the predicting and scoring sides)."""
    abs_pct = abs(pct_change)
    if abs_pct < PREDICTION_FLAT_MOVE_PCT:
        return "flat"
    direction = "up" if pct_change > 0 else "down"
    size = "big" if abs_pct >= PREDICTION_BIG_MOVE_PCT else "small"
    return f"{size}_{direction}"
