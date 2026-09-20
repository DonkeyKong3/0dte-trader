"""SQLite persistence for the signal log -- what the engine has told the
user, over time, so the dashboard can show a track record."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager

from config import DB_PATH, TZ

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    tradeable INTEGER NOT NULL,
    direction TEXT NOT NULL,
    strategy TEXT,
    score REAL NOT NULL,
    spy_price REAL,
    spx_estimate REAL,
    card_json TEXT,
    reasons_json TEXT
);
"""


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.execute(SCHEMA)


def record_cycle(
    tradeable: bool,
    direction: str,
    score: float,
    spy_price: float | None,
    spx_estimate: float | None,
    card: dict | None,
    reasons: list[str],
    now: dt.datetime | None = None,
) -> None:
    now = now or dt.datetime.now(TZ)
    with _conn() as conn:
        conn.execute(
            """INSERT INTO signals
               (timestamp, tradeable, direction, strategy, score, spy_price, spx_estimate, card_json, reasons_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now.isoformat(),
                1 if tradeable else 0,
                direction,
                (card or {}).get("strategy"),
                score,
                spy_price,
                spx_estimate,
                json.dumps(card) if card else None,
                json.dumps(reasons),
            ),
        )


def latest() -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM signals ORDER BY id DESC LIMIT 1").fetchone()
        return _row_to_dict(row) if row else None


def history(limit: int = 100) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["tradeable"] = bool(d["tradeable"])
    d["card"] = json.loads(d.pop("card_json")) if d.get("card_json") else None
    d["reasons"] = json.loads(d.pop("reasons_json")) if d.get("reasons_json") else []
    return d
