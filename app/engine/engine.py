"""Orchestrates one full analysis cycle: pull data -> compute signals ->
gate on confidence -> build a trade card (or nothing) -> persist."""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging

from config import OPENING_RANGE_MINUTES, PROXY_TICKER, TZ
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
from app.engine.spreads import build_trade_card

log = logging.getLogger(__name__)


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
    }


def run_cycle(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(TZ)

    bars = get_intraday_bars(PROXY_TICKER)
    quote = get_last_quote(PROXY_TICKER)
    chain = get_0dte_options_chain(PROXY_TICKER)
    ratio = get_spx_spy_ratio()

    if bars.empty or quote is None:
        result = {"tradeable": False, "direction": "none", "score": 0.0, "card": None, "reasons": ["No market data available"]}
        db.record_cycle(False, "none", 0.0, None, None, None, result["reasons"], now)
        return result

    result = _analyze(bars, quote.price, quote.as_of, chain, ratio, now)

    db.record_cycle(
        tradeable=result["tradeable"],
        direction=result["direction"],
        score=result["score"],
        spy_price=result["spy_price"],
        spx_estimate=result["spx_estimate"],
        card=result["card"],
        reasons=result["reasons"],
        now=now,
    )
    return result


def run_demo_cycle() -> dict:
    """Replays the most recently completed trading session's real bars and
    the nearest available options expiration, so the pipeline can be
    sanity-checked while the market is closed (or on a Tue/Thu gap day with
    no same-day SPY expiration). NOT a live signal, and never persisted to
    the signal history -- the caller/UI must label it clearly as a demo."""
    bars = get_most_recent_session_bars(PROXY_TICKER)
    if bars.empty:
        return {
            "demo": True,
            "tradeable": False,
            "direction": "none",
            "score": 0.0,
            "card": None,
            "reasons": ["No historical market data available"],
        }

    last_bar_time = bars.index[-1].to_pydatetime()
    chain = get_nearest_expiration_options_chain(PROXY_TICKER)
    ratio = get_spx_spy_ratio()

    result = _analyze(bars, float(bars["Close"].iloc[-1]), last_bar_time, chain, ratio, last_bar_time)
    result["demo"] = True
    result["session_date"] = last_bar_time.date().isoformat()
    result["chain_expiration"] = chain.expiration if chain else None
    return result
