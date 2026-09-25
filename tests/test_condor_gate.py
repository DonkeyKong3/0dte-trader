"""Regression tests for tightening iron-condor eligibility after production
data showed the model suggesting condors far more often than directional
spreads. Root cause: range_bound_score is a WEIGHTED AVERAGE across 4
signals, so one genuinely strong, directional signal can still be averaged
away by three quiet ones and clear the threshold -- "quiet on average" is
not the same thing as "genuinely quiet." The fix pairs a higher average
threshold (CONDOR_RANGE_SCORE_THRESHOLD 70->80) with a hard per-signal cap
(CONDOR_MAX_SIGNAL_STRENGTH) that can veto condor eligibility on its own.
"""
import datetime as dt

import pytest

from config import CONDOR_MAX_SIGNAL_STRENGTH, CONDOR_RANGE_SCORE_THRESHOLD, TZ
from app.data.market_data import OptionLeg, OptionsChain, bs_price
from app.engine.confidence import ConfidenceVerdict
from app.engine.signals import SignalResult
from app.engine.spreads import (
    build_trade_card,
    max_directional_signal_strength,
    range_bound_score,
    time_to_expiry_years,
)

_NEUTRAL = SignalResult("_", "neutral", 0, "quiet")


def _signals(trend_strength: float, trend_direction: str = "bullish") -> list[SignalResult]:
    return [
        SignalResult("trend", trend_direction, trend_strength, "test"),
        SignalResult("momentum", "neutral", 0, "test"),
        SignalResult("volume", "neutral", 0, "test"),
        SignalResult("opening_range", "neutral", 0, "test"),
        SignalResult("iv_skew", "neutral", 0, "test"),
    ]


def test_max_directional_signal_strength_ignores_neutral_readings():
    assert max_directional_signal_strength(_signals(0, "bullish")) == 0.0


def test_max_directional_signal_strength_picks_up_a_lone_strong_signal():
    assert max_directional_signal_strength(_signals(60, "bullish")) == 60.0


def test_range_bound_score_can_clear_threshold_despite_one_strong_signal():
    """The averaging problem this whole fix targets: one strong signal
    (60, weight 25) alongside three fully quiet ones still averages to
    above CONDOR_RANGE_SCORE_THRESHOLD."""
    score = range_bound_score(_signals(60, "bullish"))
    assert score >= CONDOR_RANGE_SCORE_THRESHOLD
    # ...which is exactly why max_directional_signal_strength exists as a
    # separate, non-averaged veto:
    assert max_directional_signal_strength(_signals(60, "bullish")) > CONDOR_MAX_SIGNAL_STRENGTH


def _quiet_rich_chain(spot: float, t_years: float, vol: float = 0.15, rate: float = 0.05) -> OptionsChain:
    calls, puts = [], []
    for strike in range(int(spot - 30), int(spot + 30) + 1):
        c_price = bs_price(spot, strike, t_years, vol, rate, True)
        p_price = bs_price(spot, strike, t_years, vol, rate, False)
        calls.append(OptionLeg(strike=float(strike), bid=c_price, ask=c_price, last=c_price, volume=50, open_interest=50, implied_vol=vol))
        puts.append(OptionLeg(strike=float(strike), bid=p_price, ask=p_price, last=p_price, volume=50, open_interest=50, implied_vol=vol))
    return OptionsChain(expiration="2026-01-06", underlying_price=spot, calls=calls, puts=puts)


def test_build_trade_card_refuses_condor_when_one_signal_is_genuinely_strong():
    """Integration-level check: even though the weighted average alone
    would qualify (see test above), build_trade_card must NOT produce an
    iron condor when a single signal is this strong -- not genuinely
    quiet, just averaged to look that way."""
    now = dt.datetime.combine(dt.date(2026, 1, 6), dt.time(10, 0), tzinfo=TZ)
    t_years = time_to_expiry_years(now, "2026-01-06")
    chain = _quiet_rich_chain(500.0, t_years)
    verdict = ConfidenceVerdict(tradeable=False, direction="none", score=18.75, signals=_signals(60, "bullish"))

    card = build_trade_card(verdict, chain, ratio=10.0, now=now)

    assert card is None


def test_build_trade_card_allows_condor_once_signal_is_genuinely_quiet():
    now = dt.datetime.combine(dt.date(2026, 1, 6), dt.time(10, 0), tzinfo=TZ)
    t_years = time_to_expiry_years(now, "2026-01-06")
    chain = _quiet_rich_chain(500.0, t_years)
    verdict = ConfidenceVerdict(tradeable=False, direction="none", score=0.0, signals=_signals(0, "bullish"))

    card = build_trade_card(verdict, chain, ratio=10.0, now=now)

    assert card is not None
    assert card.strategy == "iron_condor"
