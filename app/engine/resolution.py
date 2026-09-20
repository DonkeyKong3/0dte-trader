"""Resolves currently open tracked trades every poll cycle: re-prices each
one against a fresh chain snapshot and closes it out on profit target,
stop loss, or the hard time cutoff -- whichever comes first, exactly like
the trade card told the user to. On a loss, tags a best-effort, honestly-
scoped reason so patterns can be reviewed later; this is diagnostic data
for a human (or a future session) to act on, not an automatic rewrite of
the confidence logic.
"""
from __future__ import annotations

import datetime as dt
import logging

from config import CONFIDENCE_THRESHOLD, PROXY_TICKER, TZ
from app import db
from app.data.economic_calendar import high_impact_events_today
from app.data.market_data import get_0dte_options_chain, get_last_quote, get_spx_spy_ratio
from app.engine.spreads import reprice_trade

log = logging.getLogger(__name__)

_CREDIT_STYLE = {"bull_put_credit", "bear_call_credit", "iron_condor"}


def _pnl(trade: dict, exit_price: float) -> float:
    """Positive = profit, negative = loss, in SPX-dollar terms per spread."""
    entry = trade["entry_price"]
    if trade["strategy"] in _CREDIT_STYLE:
        return entry - exit_price  # credit received minus cost to close
    return exit_price - entry  # debit spreads: current resale value minus what was paid


def _tag_loss_reasons(trade: dict, now: dt.datetime, current_spy_price: float | None) -> list[str]:
    tags: list[str] = []

    entry_spy = trade.get("entry_spy_price")
    if entry_spy and current_spy_price and trade["direction"] in ("bullish", "bearish"):
        moved_up = current_spy_price > entry_spy
        predicted_up = trade["direction"] == "bullish"
        pct = (current_spy_price - entry_spy) / entry_spy * 100
        if moved_up != predicted_up:
            tags.append(
                f"Directional call wrong: predicted {trade['direction']}, "
                f"SPY moved {entry_spy:.2f} -> {current_spy_price:.2f} ({pct:+.2f}%)"
            )
        else:
            tags.append(
                f"Direction was right ({trade['direction']}, SPY {pct:+.2f}%) but the move wasn't "
                f"enough/fast enough to avoid the stop -- possible sizing/width/target issue, not a bad call"
            )

    events = high_impact_events_today(now)
    if events:
        names = ", ".join(e.name for e in events)
        tags.append(f"High-impact economic event scheduled same day: {names} -- correlation only, not a claimed cause")

    score = trade.get("confidence_score")
    if score is not None and score < CONFIDENCE_THRESHOLD + 10:
        tags.append(f"Entered on borderline confidence ({score:.1f}, threshold {CONFIDENCE_THRESHOLD}) -- consider raising the bar")

    return tags


def _resolve(trade: dict, exit_price: float, exit_reason_code: str, now: dt.datetime, spy_price: float | None) -> None:
    pnl = _pnl(trade, exit_price)
    status = "won" if pnl >= 0 else "lost"
    reason_tags = _tag_loss_reasons(trade, now, spy_price) if status == "lost" else []
    db.resolve_trade(trade["id"], status, exit_price, exit_reason_code, pnl, reason_tags, now)
    log.info("Resolved trade %s: %s (%s), pnl=%.2f", trade["id"], status, exit_reason_code, pnl)


def check_open_trades(now: dt.datetime | None = None) -> None:
    now = now or dt.datetime.now(TZ)
    trades = db.open_trades()
    if not trades:
        return

    chain = get_0dte_options_chain(PROXY_TICKER)
    ratio = get_spx_spy_ratio()
    quote = get_last_quote(PROXY_TICKER)
    spy_price = quote.price if quote else None

    for trade in trades:
        force_close_by = dt.datetime.fromisoformat(trade["force_close_by"])
        current_price = reprice_trade(trade, chain, ratio)

        if current_price is None:
            if now >= force_close_by:
                # Couldn't get a fresh quote by the hard cutoff -- don't
                # silently drop it, and don't fabricate a price either.
                db.resolve_trade(trade["id"], "unresolved", None, "unresolved", None, [
                    "Could not re-price at/after the force-close cutoff (no usable chain data)"
                ], now)
            continue

        if trade["strategy"] in _CREDIT_STYLE:
            if current_price <= trade["profit_target_price"]:
                _resolve(trade, current_price, "profit_target", now, spy_price)
                continue
            if current_price >= trade["stop_loss_price"]:
                _resolve(trade, current_price, "stop_loss", now, spy_price)
                continue
        else:
            if current_price >= trade["profit_target_price"]:
                _resolve(trade, current_price, "profit_target", now, spy_price)
                continue
            if current_price <= trade["stop_loss_price"]:
                _resolve(trade, current_price, "stop_loss", now, spy_price)
                continue

        if now >= force_close_by:
            _resolve(trade, current_price, "time_cutoff", now, spy_price)
