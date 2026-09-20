import datetime as dt

from config import CONFIDENCE_THRESHOLD, NO_NEW_TRADES_AFTER, TZ
from app.data.economic_calendar import _nth_weekday
from app.engine.confidence import evaluate
from app.engine.signals import SignalResult


def _quiet_afternoon(year=2030, month=1) -> dt.datetime:
    """A Tuesday afternoon with no rule-based or curated events nearby."""
    d = dt.date(year, month, 1)
    while d.weekday() != 1:  # Tuesday
        d += dt.timedelta(days=1)
    return dt.datetime.combine(d, dt.time(13, 0), tzinfo=TZ)


def _all_neutral():
    return [
        SignalResult("trend", "neutral", 10, "flat"),
        SignalResult("momentum", "neutral", 10, "flat"),
        SignalResult("volume", "neutral", 10, "flat"),
        SignalResult("opening_range", "neutral", 0, "not established"),
        SignalResult("iv_skew", "neutral", 10, "flat"),
    ]


def _strong_bullish():
    # Weighted sum (trend 25*1.0 + momentum 20*0.95 + volume 20*0.9 +
    # opening_range 15*0.8 = 74) clears CONFIDENCE_THRESHOLD (70).
    return [
        SignalResult("trend", "bullish", 100, "aligned up"),
        SignalResult("momentum", "bullish", 95, "RSI hot"),
        SignalResult("volume", "bullish", 90, "confirmed"),
        SignalResult("opening_range", "bullish", 80, "broke high"),
        SignalResult("iv_skew", "neutral", 10, "flat"),
    ]


def test_no_trade_when_all_signals_neutral():
    verdict = evaluate(_all_neutral(), now=_quiet_afternoon())
    assert verdict.tradeable is False
    assert verdict.direction == "none"
    assert verdict.hard_block is False


def test_bullish_direction_when_bullish_signals_dominate():
    verdict = evaluate(_strong_bullish(), now=_quiet_afternoon())
    assert verdict.tradeable is True
    assert verdict.direction == "bullish"
    assert verdict.score >= CONFIDENCE_THRESHOLD


def test_weak_signals_stay_below_threshold_and_are_not_tradeable():
    weak = [
        SignalResult("trend", "bullish", 20, "barely"),
        SignalResult("momentum", "bullish", 15, "barely"),
        SignalResult("volume", "neutral", 5, "quiet"),
        SignalResult("opening_range", "neutral", 0, "not established"),
        SignalResult("iv_skew", "neutral", 5, "flat"),
    ]
    verdict = evaluate(weak, now=_quiet_afternoon())
    assert verdict.tradeable is False
    assert verdict.score < CONFIDENCE_THRESHOLD


def test_cutoff_blocks_new_entries_regardless_of_score():
    late = _quiet_afternoon().replace(hour=NO_NEW_TRADES_AFTER[0], minute=NO_NEW_TRADES_AFTER[1] + 5)
    verdict = evaluate(_strong_bullish(), now=late)
    assert verdict.tradeable is False
    assert verdict.hard_block is True
    assert any("cutoff" in r.lower() for r in verdict.reasons)


def test_jobless_claims_blackout_blocks_new_entries():
    # Weekly initial jobless claims: every Thursday, 8:30am ET.
    d = dt.date(2030, 1, 1)
    while d.weekday() != 3:  # Thursday
        d += dt.timedelta(days=1)
    inside_blackout = dt.datetime.combine(d, dt.time(8, 40), tzinfo=TZ)
    verdict = evaluate(_strong_bullish(), now=inside_blackout)
    assert verdict.tradeable is False
    assert verdict.hard_block is True
    assert any("blackout" in r.lower() for r in verdict.reasons)


def test_nfp_day_penalty_reduces_score_outside_blackout():
    first_friday = _nth_weekday(2030, 3, 4, 1)
    nfp_afternoon = dt.datetime.combine(first_friday, dt.time(13, 0), tzinfo=TZ)
    normal_day = _quiet_afternoon(2030, 3).replace(day=first_friday.day - 1 if first_friday.day > 1 else first_friday.day + 3)

    verdict_event_day = evaluate(_strong_bullish(), now=nfp_afternoon)
    verdict_normal_day = evaluate(_strong_bullish(), now=normal_day)

    assert verdict_event_day.score < verdict_normal_day.score
