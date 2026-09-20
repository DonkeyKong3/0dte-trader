"""Market-hours polling loop. Runs `engine.run_cycle` every
POLL_INTERVAL_SECONDS while the market is open on a weekday, and skips
entirely outside that window (no point burning API calls on a closed
market, and no point suggesting 0DTE trades when there's no 0DTE).
"""
from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from config import MARKET_CLOSE, MARKET_OPEN, POLL_INTERVAL_SECONDS, TZ
from app.engine.engine import run_cycle

log = logging.getLogger(__name__)


def _market_is_open(now: dt.datetime) -> bool:
    if now.weekday() >= 5:  # Sat/Sun
        return False
    open_t = now.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    close_t = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)
    return open_t <= now <= close_t


def _tick() -> None:
    now = dt.datetime.now(TZ)
    if not _market_is_open(now):
        return
    try:
        run_cycle(now)
    except Exception:
        log.exception("Signal cycle failed")


def start() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.add_job(_tick, "interval", seconds=POLL_INTERVAL_SECONDS, id="signal_cycle", max_instances=1)
    scheduler.start()
    return scheduler
