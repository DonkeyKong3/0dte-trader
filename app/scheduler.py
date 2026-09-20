"""Market-hours polling loop. Runs `engine.run_cycle` every
POLL_INTERVAL_SECONDS while the market is open on a weekday, and skips
entirely outside that window (no point burning API calls on a closed
market, and no point suggesting 0DTE trades when there's no 0DTE).
"""
from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from config import MARKET_CLOSE, MARKET_OPEN, POLL_INTERVAL_SECONDS, PREDICTION_HORIZON_MINUTES, TZ
from app.engine.engine import run_cycle
from app.engine.resolution import check_open_trades, resolve_predictions

log = logging.getLogger(__name__)


def _market_is_open(now: dt.datetime) -> bool:
    if now.weekday() >= 5:  # Sat/Sun
        return False
    open_t = now.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    close_t = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)
    return open_t <= now <= close_t


def _resolution_window_open(now: dt.datetime) -> bool:
    """Resolving open trades/predictions runs a bit past market close too --
    a trade or prediction made near end-of-day still needs to resolve after
    the close (a prediction's horizon can land up to PREDICTION_HORIZON_
    MINUTES past 4pm), even though no *new* cycle should start by then."""
    if now.weekday() >= 5:
        return False
    open_t = now.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    grace_close = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0) + dt.timedelta(
        minutes=PREDICTION_HORIZON_MINUTES
    )
    return open_t <= now <= grace_close


def _tick() -> None:
    now = dt.datetime.now(TZ)

    if _resolution_window_open(now):
        try:
            check_open_trades(now)  # resolve existing trades before deciding whether to open a new one
        except Exception:
            log.exception("Trade resolution failed")
        try:
            resolve_predictions(now)
        except Exception:
            log.exception("Prediction resolution failed")

    if _market_is_open(now):
        try:
            run_cycle(now)
        except Exception:
            log.exception("Signal cycle failed")


def start() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.add_job(_tick, "interval", seconds=POLL_INTERVAL_SECONDS, id="signal_cycle", max_instances=1)
    scheduler.start()
    return scheduler
