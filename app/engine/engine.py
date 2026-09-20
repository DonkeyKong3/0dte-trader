"""Orchestrates one full analysis cycle: pull data -> compute signals ->
gate on confidence -> build a trade card (or nothing) -> persist."""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging

from config import OPENING_RANGE_MINUTES, PROXY_TICKER, TZ
from app import db
from app.data.market_data import get_0dte_options_chain, get_intraday_bars, get_last_quote, get_spx_spy_ratio
from app.engine import signals as sig
from app.engine.confidence import evaluate
from app.engine.spreads import build_trade_card

log = logging.getLogger(__name__)


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

    signal_list = [
        sig.trend_signal(bars),
        sig.momentum_signal(bars),
        sig.volume_signal(bars),
        sig.opening_range_signal(bars, OPENING_RANGE_MINUTES),
        sig.iv_skew_signal(chain),
    ]

    verdict = evaluate(signal_list, now)
    card = build_trade_card(verdict, chain, ratio, now)

    spx_estimate = quote.price * ratio if ratio else None
    card_dict = dataclasses.asdict(card) if card else None

    reasons = list(verdict.reasons)
    if card is None and verdict.tradeable:
        reasons.append("Confident direction found, but no usable options chain/strikes to build a spread")

    db.record_cycle(
        tradeable=card is not None,
        direction=card.direction if card else "none",
        score=verdict.score,
        spy_price=quote.price,
        spx_estimate=spx_estimate,
        card=card_dict,
        reasons=reasons,
        now=now,
    )

    return {
        "tradeable": card is not None,
        "direction": card.direction if card else "none",
        "score": round(verdict.score, 1),
        "card": card_dict,
        "reasons": reasons,
        "signals": [dataclasses.asdict(s) for s in signal_list],
        "spy_price": quote.price,
        "spx_estimate": spx_estimate,
        "data_as_of": quote.as_of.isoformat(),
    }
