"""Orchestrates one full analysis cycle: pull data -> compute signals ->
gate on confidence -> build a trade card (or nothing) -> persist."""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging

from config import (
    FORCE_CLOSE_BY,
    NO_NEW_TRADES_AFTER,
    OPENING_RANGE_MINUTES,
    PREDICTION_HORIZON_MINUTES,
    PROXY_TICKER,
    TZ,
)
from app import db
from app.data.market_data import (
    get_0dte_options_chain,
    get_intraday_bars,
    get_last_quote,
    get_most_recent_session_bars,
    get_nearest_expiration_options_chain,
    get_spx_spy_ratio,
)
from app.engine import signals as sig
from app.engine.confidence import evaluate
from app.engine.prediction import (
    LABELS as PREDICTION_LABELS,
    atr_expected_move_pct,
    bucket_side,
    classify_realized_move,
    iv_expected_move_pct,
    predict_movement,
)
from app.engine.spreads import build_trade_card

log = logging.getLogger(__name__)

PREDICTION_HORIZON_YEARS = (PREDICTION_HORIZON_MINUTES * 60) / (365 * 24 * 3600)


def _analyze(bars, quote_price: float, quote_as_of: dt.datetime, chain, ratio: float | None, now: dt.datetime) -> dict:
    signal_list = [
        sig.trend_signal(bars),
        sig.momentum_signal(bars),
        sig.volume_signal(bars),
        sig.opening_range_signal(bars, OPENING_RANGE_MINUTES),
        sig.iv_skew_signal(chain),
    ]

    verdict = evaluate(signal_list, now)
    card = build_trade_card(verdict, chain, ratio, now)

    # Magnitude evidence for the prediction, independent of the directional
    # signal vote: prefer the options market's own IV-implied expected move,
    # fall back to realized ATR when there's no usable chain.
    expected_move_pct = iv_expected_move_pct(chain, quote_price, PREDICTION_HORIZON_YEARS)
    magnitude_source = "iv"
    if expected_move_pct is None:
        expected_move_pct = atr_expected_move_pct(bars, quote_price, PREDICTION_HORIZON_MINUTES)
        magnitude_source = "atr" if expected_move_pct is not None else "none"
    prediction = predict_movement(signal_list, expected_move_pct, magnitude_source)

    spx_estimate = quote_price * ratio if ratio else None
    card_dict = dataclasses.asdict(card) if card else None

    reasons = list(verdict.reasons)
    if card is None and verdict.tradeable:
        reasons.append("Confident direction found, but no usable options chain/strikes to build a spread")

    return {
        "tradeable": card is not None,
        "direction": card.direction if card else "none",
        "score": round(verdict.score, 1),
        "card": card_dict,
        "reasons": reasons,
        "signals": [dataclasses.asdict(s) for s in signal_list],
        "spy_price": quote_price,
        "spx_estimate": spx_estimate,
        "data_as_of": quote_as_of.isoformat(),
        "prediction": {
            "bucket": prediction.bucket,
            "label": PREDICTION_LABELS[prediction.bucket],
            "confidence": prediction.confidence,
            "net_score": prediction.net_score,
            "expected_move_pct": prediction.expected_move_pct,
            "magnitude_source": prediction.magnitude_source,
        },
    }


def run_cycle(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(TZ)

    bars = get_intraday_bars(PROXY_TICKER)
    quote = get_last_quote(PROXY_TICKER)
    chain = get_0dte_options_chain(PROXY_TICKER)
    ratio = get_spx_spy_ratio()

    if bars.empty or quote is None:
        result = {
            "tradeable": False,
            "direction": "none",
            "score": 0.0,
            "card": None,
            "reasons": ["No market data available"],
            "prediction": None,
        }
        db.record_cycle(False, "none", 0.0, None, None, None, result["reasons"], now=now)
        return result

    result = _analyze(bars, quote.price, quote.as_of, chain, ratio, now)
    prediction_resolve_by = now + dt.timedelta(minutes=PREDICTION_HORIZON_MINUTES)

    db.record_cycle(
        tradeable=result["tradeable"],
        direction=result["direction"],
        score=result["score"],
        spy_price=result["spy_price"],
        spx_estimate=result["spx_estimate"],
        card=result["card"],
        reasons=result["reasons"],
        predicted_bucket=result["prediction"]["bucket"],
        predicted_confidence=result["prediction"]["confidence"],
        predicted_net_score=result["prediction"]["net_score"],
        predicted_expected_move_pct=result["prediction"]["expected_move_pct"],
        predicted_magnitude_source=result["prediction"]["magnitude_source"],
        prediction_resolve_by=prediction_resolve_by,
        now=now,
    )

    # Track at most one open suggestion at a time -- if a trade is already
    # open, a fresh confident card is still shown live but not separately
    # paper-tracked (mirrors actually only holding one position at once,
    # and keeps win-rate stats meaningful instead of over-counting the same
    # real-world trade every poll while conditions persist).
    if result["card"] and not db.open_trades():
        force_close_by = now.replace(hour=FORCE_CLOSE_BY[0], minute=FORCE_CLOSE_BY[1], second=0, microsecond=0)
        db.open_trade(result["card"], result["signals"], force_close_by, now, result["spy_price"])

    return result


def run_demo_cycle() -> dict:
    """Replays the most recently completed trading session's real bars and
    the nearest available options expiration, so the pipeline can be
    sanity-checked while the market is closed (or on a Tue/Thu gap day with
    no same-day SPY expiration). NOT a live signal, and never persisted to
    the signal history -- the caller/UI must label it clearly as a demo.

    Evaluated one minute before the real NO_NEW_TRADES_AFTER cutoff (not at
    the session's last bar, which is always past it) and using only bars up
    to that point -- otherwise every demo run would trivially fail on "past
    cutoff" regardless of the actual signals, and indicators would be
    computed with lookahead into bars from later in the day.
    """
    session_bars = get_most_recent_session_bars(PROXY_TICKER)
    if session_bars.empty:
        return {
            "demo": True,
            "tradeable": False,
            "direction": "none",
            "score": 0.0,
            "card": None,
            "reasons": ["No historical market data available"],
            "prediction": None,
        }

    session_date = session_bars.index[0].date()
    eval_dt = dt.datetime.combine(
        session_date, dt.time(NO_NEW_TRADES_AFTER[0], NO_NEW_TRADES_AFTER[1]), tzinfo=TZ
    ) - dt.timedelta(minutes=1)

    bars = session_bars[session_bars.index <= eval_dt]
    if bars.empty:  # cutoff earlier than the session's first bar -- fall back to everything available
        bars = session_bars
        eval_dt = bars.index[-1].to_pydatetime()

    chain = get_nearest_expiration_options_chain(PROXY_TICKER)
    ratio = get_spx_spy_ratio()

    result = _analyze(bars, float(bars["Close"].iloc[-1]), bars.index[-1].to_pydatetime(), chain, ratio, eval_dt)
    result["demo"] = True
    result["session_date"] = session_date.isoformat()
    result["evaluated_at"] = eval_dt.strftime("%H:%M ET")
    result["chain_expiration"] = chain.expiration if chain else None

    # Hindsight: we already have the rest of that session's real bars, so
    # show immediately how the prediction actually played out -- no need
    # to wait PREDICTION_HORIZON_MINUTES like the live pipeline does.
    horizon_dt = eval_dt + dt.timedelta(minutes=PREDICTION_HORIZON_MINUTES)
    future = session_bars[session_bars.index >= horizon_dt]
    if not future.empty and result.get("prediction"):
        entry_price = float(bars["Close"].iloc[-1])
        future_price = float(future["Close"].iloc[0])
        pct_change = (future_price - entry_price) / entry_price * 100
        realized_bucket = classify_realized_move(pct_change)
        predicted_bucket = result["prediction"]["bucket"]
        result["prediction_hindsight"] = {
            "bucket": realized_bucket,
            "label": PREDICTION_LABELS[realized_bucket],
            "realized_pct_change": round(pct_change, 4),
            "correct": realized_bucket == predicted_bucket,
            "direction_correct": bucket_side(realized_bucket) == bucket_side(predicted_bucket),
            "checked_at": future.index[0].strftime("%H:%M ET"),
        }
    return result
