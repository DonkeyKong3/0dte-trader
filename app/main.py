from __future__ import annotations

import datetime as dt
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from config import TZ
from app import db
from app.data.economic_calendar import events_for_date
from app.engine.engine import run_cycle, run_demo_cycle
from app.scheduler import start as start_scheduler

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(title="SPX 0DTE Spread Signals")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(Exception)
def _json_on_unhandled_error(request: Request, exc: Exception):
    """An unhandled exception otherwise returns a plain-text 500, which
    breaks the dashboard's `res.json()` and surfaces as a generic 'could
    not load' with no clue why. Always return JSON instead so a bad data
    quote degrades to a visible reason, not a silent frontend failure."""
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"tradeable": False, "direction": "none", "score": 0.0, "card": None, "reasons": [f"Server error: {exc}"]},
    )

_scheduler = None


@app.on_event("startup")
def _startup() -> None:
    global _scheduler
    db.init_db()
    _scheduler = start_scheduler()


@app.get("/api/signal")
def get_signal():
    latest = db.latest()
    if latest is None:
        return {"tradeable": False, "direction": "none", "score": 0.0, "card": None, "reasons": ["No data yet -- waiting for first cycle"]}
    return latest


@app.post("/api/refresh")
def refresh():
    """Force an immediate cycle instead of waiting for the next poll tick."""
    return run_cycle()


@app.post("/api/demo")
def demo():
    """Replays the most recent completed session's real data through the
    same pipeline, for sanity-checking while the market is closed. Not
    persisted to signal history."""
    return run_demo_cycle()


@app.get("/api/history")
def get_history(limit: int = 50):
    return db.history(limit)


@app.get("/api/trades")
def get_trades(limit: int = 50):
    return db.trades_history(limit)


@app.get("/api/stats")
def get_stats():
    return db.trade_stats()


@app.get("/api/econ-today")
def econ_today():
    now = dt.datetime.now(TZ)
    events = events_for_date(now.date())
    return [{"name": e.name, "time_et": e.time_et.strftime("%H:%M"), "impact": e.impact} for e in events]


app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def index():
    return FileResponse("app/static/index.html")
