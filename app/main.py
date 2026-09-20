from __future__ import annotations

import datetime as dt
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import TZ
from app import db
from app.data.economic_calendar import events_for_date
from app.engine.engine import run_cycle
from app.scheduler import start as start_scheduler

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="SPX 0DTE Spread Signals")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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


@app.get("/api/history")
def get_history(limit: int = 50):
    return db.history(limit)


@app.get("/api/econ-today")
def econ_today():
    now = dt.datetime.now(TZ)
    events = events_for_date(now.date())
    return [{"name": e.name, "time_et": e.time_et.strftime("%H:%M"), "impact": e.impact} for e in events]


app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def index():
    return FileResponse("app/static/index.html")
