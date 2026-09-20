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
from app.engine.prediction import LABELS as PREDICTION_LABELS
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

def _with_prediction(row: dict) -> dict:
    """DB rows store flat predicted_* columns (simpler SQL); the API always
    exposes a nested "prediction" object instead, matching what a live
    /api/refresh or /api/demo cycle returns, so the frontend has one shape
    to render regardless of which endpoint it came from."""
    row = dict(row)
    bucket = row.get("predicted_bucket")
    row["prediction"] = (
        {
            "bucket": bucket,
            "label": PREDICTION_LABELS.get(bucket, bucket),
            "confidence": row.get("predicted_confidence"),
            "net_score": row.get("predicted_net_score"),
        }
        if bucket
        else None
    )
    return row


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
        return {
            "tradeable": False,
            "direction": "none",
            "score": 0.0,
            "card": None,
            "reasons": ["No data yet -- waiting for first cycle"],
            "prediction": None,
        }
    return _with_prediction(latest)


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
    return [_with_prediction(row) for row in db.history(limit)]


@app.get("/api/prediction-stats")
def get_prediction_stats():
    return db.prediction_stats()


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
