import datetime as dt

import pandas as pd
import pytest

from config import CONDOR_MIN_IV, CONDOR_RANGE_SCORE_THRESHOLD, CONFIDENCE_THRESHOLD, TZ
from app.data.market_data import OptionLeg, OptionsChain, bs_price
from app.engine import engine as eng
from app.engine.spreads import time_to_expiry_years


def _flat_bars(session_date: dt.date, periods: int = 60) -> pd.DataFrame:
    start = dt.datetime.combine(session_date, dt.time(9, 30), tzinfo=TZ)
    idx = pd.date_range(start, periods=periods, freq="1min", tz=TZ)
    return pd.DataFrame({"Open": 500.0, "High": 500.2, "Low": 499.8, "Close": 500.0, "Volume": 1000}, index=idx)


def _quiet_rich_chain(spot: float, t_years: float, vol: float = 0.15, rate: float = 0.05) -> OptionsChain:
    """A chain wide/rich enough to be iron-condor eligible: strikes span far
    enough from spot to find a ~0.18-delta short leg on each side, and the
    priced-in vol clears CONDOR_MIN_IV."""
    calls, puts = [], []
    for strike in range(int(spot - 30), int(spot + 30) + 1):
        c_price = bs_price(spot, strike, t_years, vol, rate, True)
        p_price = bs_price(spot, strike, t_years, vol, rate, False)
        calls.append(OptionLeg(strike=float(strike), bid=c_price, ask=c_price, last=c_price, volume=50, open_interest=50, implied_vol=vol))
        puts.append(OptionLeg(strike=float(strike), bid=p_price, ask=p_price, last=p_price, volume=50, open_interest=50, implied_vol=vol))
    return OptionsChain(expiration="2026-01-06", underlying_price=spot, calls=calls, puts=puts)


def test_iron_condor_row_score_matches_the_cards_own_confidence():
    """Regression test for the mismatch the user spotted between the signal
    history table and the trades it actually suggests: an iron condor is
    gated on range_bound_score, a completely different number from the
    directional verdict.score (which is necessarily BELOW
    CONFIDENCE_THRESHOLD whenever a condor fires -- that's exactly why the
    directional branch was skipped in favor of checking for a condor).
    Before the fix, `_analyze` always recorded verdict.score as the row's
    top-level "score", so the history table showed a low confidence number
    next to a suggested Iron Condor -- looking like the suggestion
    contradicted its own listed confidence, when really two different
    scores were just being conflated into one field."""
    session_date = dt.date(2026, 1, 6)
    now = dt.datetime.combine(session_date, dt.time(10, 0), tzinfo=TZ)
    bars = _flat_bars(session_date)
    spot = 500.0

    t_years = time_to_expiry_years(now, "2026-01-06")
    chain = _quiet_rich_chain(spot, t_years)

    result = eng._analyze(bars, spot, now, chain, ratio=10.0, now=now)

    assert result["card"] is not None
    assert result["card"]["strategy"] == "iron_condor"
    # The directional gate must have been skipped (flat bars -> no
    # directional consensus) -- confirms this row exercises the condor
    # path, not a coincidental directional trade.
    assert result["card"]["confidence_score"] >= CONDOR_RANGE_SCORE_THRESHOLD

    # The core assertion: the row's score is whatever actually gated the
    # card that's shown, not the (much lower) directional score.
    assert result["score"] == result["card"]["confidence_score"]
    assert result["score"] >= CONFIDENCE_THRESHOLD or result["score"] >= CONDOR_RANGE_SCORE_THRESHOLD
