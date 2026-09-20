"""Combines individual signals into one gated confidence verdict.

Design goal: default to silence. A trade is only ever suggested when the
weighted evidence clearly favors one direction AND we're not inside an
economic-event blackout AND we're within the trading window.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from config import (
    CONFIDENCE_THRESHOLD,
    ECON_BLACKOUT_MINUTES_AFTER,
    ECON_BLACKOUT_MINUTES_BEFORE,
    ECON_EVENT_DAY_PENALTY,
    NO_NEW_TRADES_AFTER,
    SIGNAL_WEIGHTS,
    TZ,
)
from app.data.economic_calendar import high_impact_events_today, in_blackout_window
from app.engine.signals import SignalResult


@dataclass
class ConfidenceVerdict:
    tradeable: bool
    direction: str  # "bullish" | "bearish" | "none"
    score: float  # 0-100
    signals: list[SignalResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)  # why gated, if gated
    # True when gating is due to timing/news (blackout, past cutoff) -- these
    # also rule out an iron condor, unlike a merely-low directional score.
    hard_block: bool = False


def weighted_direction_scores(signals: list[SignalResult]) -> tuple[float, float]:
    """(bullish_score, bearish_score), each 0-100, weighted by SIGNAL_WEIGHTS.
    Shared with prediction.py so the movement prediction is built on the
    exact same signal consensus as the trade-confidence gate, not a second,
    inconsistent model."""
    bullish = 0.0
    bearish = 0.0
    for sig in signals:
        weight = SIGNAL_WEIGHTS.get(sig.name, 0)
        contribution = weight * (sig.strength / 100)
        if sig.direction == "bullish":
            bullish += contribution
        elif sig.direction == "bearish":
            bearish += contribution
    return bullish, bearish


def evaluate(signals: list[SignalResult], now: dt.datetime | None = None) -> ConfidenceVerdict:
    now = now or dt.datetime.now(TZ)
    reasons: list[str] = []

    bullish_score, bearish_score = weighted_direction_scores(signals)
    if bullish_score >= bearish_score:
        direction, score = "bullish", bullish_score
    else:
        direction, score = "bearish", bearish_score

    # Economic-event day penalty (event exists today but we're not in the
    # tight blackout window) -- makes the bar higher, doesn't outright block.
    if high_impact_events_today(now):
        score = max(0.0, score - ECON_EVENT_DAY_PENALTY)
        reasons.append("High-impact economic event scheduled today (confidence penalty applied)")

    # Hard blackout around the event itself.
    blackout_event = in_blackout_window(now, ECON_BLACKOUT_MINUTES_BEFORE, ECON_BLACKOUT_MINUTES_AFTER)
    if blackout_event:
        reasons.append(
            f"Inside blackout window for {blackout_event.name} at {blackout_event.time_et.strftime('%H:%M')} ET -- no new entries"
        )
        return ConfidenceVerdict(False, "none", score, signals, reasons, hard_block=True)

    # Trading-window cutoff (0DTE gamma risk ramps late in the day).
    cutoff = now.replace(hour=NO_NEW_TRADES_AFTER[0], minute=NO_NEW_TRADES_AFTER[1], second=0, microsecond=0)
    if now >= cutoff:
        reasons.append(f"Past {cutoff.strftime('%H:%M')} ET cutoff for new 0DTE entries")
        return ConfidenceVerdict(False, "none", score, signals, reasons, hard_block=True)

    if score < CONFIDENCE_THRESHOLD:
        reasons.append(f"Confidence {score:.1f} below threshold {CONFIDENCE_THRESHOLD}")
        return ConfidenceVerdict(False, "none", score, signals, reasons, hard_block=False)

    return ConfidenceVerdict(True, direction, score, signals, reasons)
