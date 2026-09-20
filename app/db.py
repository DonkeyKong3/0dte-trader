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
    reasons_json TEXT,
    predicted_bucket TEXT,
    predicted_confidence REAL,
    predicted_net_score REAL,
    prediction_resolve_by TEXT,
    prediction_resolved_at TEXT,
    realized_bucket TEXT,
    realized_pct_change REAL,
    prediction_correct INTEGER,
    prediction_direction_correct INTEGER
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at TEXT NOT NULL,
    resolved_at TEXT,
    status TEXT NOT NULL DEFAULT 'open',   -- open | won | lost | unresolved
    strategy TEXT NOT NULL,
    direction TEXT NOT NULL,
    expiration TEXT NOT NULL,
    short_strike REAL,
    long_strike REAL,
    call_short_strike REAL,
    call_long_strike REAL,
    put_short_strike REAL,
    put_long_strike REAL,
    width REAL,
    entry_price REAL NOT NULL,
    entry_spy_price REAL,
    max_profit REAL,
    max_loss REAL,
    profit_target_price REAL,
    stop_loss_price REAL,
    force_close_by TEXT NOT NULL,          -- ISO datetime
    confidence_score REAL,
    exit_price REAL,
    exit_reason_code TEXT,                 -- profit_target | stop_loss | time_cutoff | unresolved
    pnl REAL,
    reason_tags_json TEXT,
    signals_json TEXT,
    rationale_json TEXT
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
        conn.executescript(SCHEMA)


def record_cycle(
    tradeable: bool,
    direction: str,
    score: float,
    spy_price: float | None,
    spx_estimate: float | None,
    card: dict | None,
    reasons: list[str],
    predicted_bucket: str | None = None,
    predicted_confidence: float | None = None,
    predicted_net_score: float | None = None,
    prediction_resolve_by: dt.datetime | None = None,
    now: dt.datetime | None = None,
) -> None:
    now = now or dt.datetime.now(TZ)
    with _conn() as conn:
        conn.execute(
            """INSERT INTO signals
               (timestamp, tradeable, direction, strategy, score, spy_price, spx_estimate, card_json, reasons_json,
                predicted_bucket, predicted_confidence, predicted_net_score, prediction_resolve_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                predicted_bucket,
                predicted_confidence,
                predicted_net_score,
                prediction_resolve_by.isoformat() if prediction_resolve_by else None,
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
    # `d.pop(...) if d.get(...) else None` only pops inside the truthy
    # branch -- when card_json/reasons_json is NULL, the pop never runs and
    # the raw *_json key leaks into the dict (and every API response).
    card_json = d.pop("card_json", None)
    d["card"] = json.loads(card_json) if card_json else None
    reasons_json = d.pop("reasons_json", None)
    d["reasons"] = json.loads(reasons_json) if reasons_json else []
    if d.get("prediction_correct") is not None:
        d["prediction_correct"] = bool(d["prediction_correct"])
    if d.get("prediction_direction_correct") is not None:
        d["prediction_direction_correct"] = bool(d["prediction_direction_correct"])
    return d


# --- Prediction accuracy tracking ---

def unresolved_due_predictions(now: dt.datetime) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT * FROM signals
               WHERE prediction_resolve_by IS NOT NULL
                 AND prediction_resolved_at IS NULL
                 AND prediction_resolve_by <= ?""",
            (now.isoformat(),),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def resolve_prediction(
    signal_id: int,
    realized_bucket: str,
    realized_pct_change: float,
    correct: bool,
    direction_correct: bool,
    resolved_at: dt.datetime,
) -> None:
    with _conn() as conn:
        conn.execute(
            """UPDATE signals SET prediction_resolved_at = ?, realized_bucket = ?, realized_pct_change = ?,
               prediction_correct = ?, prediction_direction_correct = ? WHERE id = ?""",
            (resolved_at.isoformat(), realized_bucket, realized_pct_change, 1 if correct else 0, 1 if direction_correct else 0, signal_id),
        )


def prediction_stats() -> dict:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM signals WHERE prediction_resolved_at IS NOT NULL").fetchall()

    resolved = [_row_to_dict(r) for r in rows]
    total = len(resolved)
    exact = sum(1 for r in resolved if r["prediction_correct"])
    direction_ok = sum(1 for r in resolved if r["prediction_direction_correct"])

    by_bucket: dict[str, dict] = {}
    for r in resolved:
        b = by_bucket.setdefault(r["predicted_bucket"], {"total": 0, "correct": 0, "direction_correct": 0})
        b["total"] += 1
        b["correct"] += 1 if r["prediction_correct"] else 0
        b["direction_correct"] += 1 if r["prediction_direction_correct"] else 0
    for b in by_bucket.values():
        b["accuracy"] = round(b["correct"] / b["total"] * 100, 1) if b["total"] else None
        b["direction_accuracy"] = round(b["direction_correct"] / b["total"] * 100, 1) if b["total"] else None

    return {
        "total_resolved": total,
        "exact_accuracy": round(exact / total * 100, 1) if total else None,
        "direction_accuracy": round(direction_ok / total * 100, 1) if total else None,
        "by_bucket": by_bucket,
    }


# --- Trade tracking (paper-tracked outcomes of confident suggestions) ---

def open_trade(
    card: dict,
    signals_snapshot: list[dict],
    force_close_by: dt.datetime,
    now: dt.datetime,
    entry_spy_price: float | None,
) -> int:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO trades
               (opened_at, status, strategy, direction, expiration,
                short_strike, long_strike, call_short_strike, call_long_strike,
                put_short_strike, put_long_strike, width, entry_price, entry_spy_price,
                max_profit, max_loss, profit_target_price, stop_loss_price, force_close_by,
                confidence_score, signals_json, rationale_json)
               VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now.isoformat(),
                card["strategy"],
                card["direction"],
                card["expiration"],
                card.get("short_strike"),
                card.get("long_strike"),
                card.get("call_short_strike"),
                card.get("call_long_strike"),
                card.get("put_short_strike"),
                card.get("put_long_strike"),
                card.get("width"),
                card["est_credit_debit"],
                entry_spy_price,
                card.get("max_profit"),
                card.get("max_loss"),
                card.get("profit_target_price"),
                card.get("stop_loss_price"),
                force_close_by.isoformat(),
                card.get("confidence_score"),
                json.dumps(signals_snapshot),
                json.dumps(card.get("rationale") or []),
            ),
        )
        return cur.lastrowid


def open_trades() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM trades WHERE status = 'open' ORDER BY id").fetchall()
        return [_trade_row_to_dict(r) for r in rows]


def resolve_trade(
    trade_id: int,
    status: str,
    exit_price: float | None,
    exit_reason_code: str,
    pnl: float | None,
    reason_tags: list[str],
    resolved_at: dt.datetime,
) -> None:
    with _conn() as conn:
        conn.execute(
            """UPDATE trades SET status = ?, resolved_at = ?, exit_price = ?, exit_reason_code = ?,
               pnl = ?, reason_tags_json = ? WHERE id = ?""",
            (status, resolved_at.isoformat(), exit_price, exit_reason_code, pnl, json.dumps(reason_tags), trade_id),
        )


def trades_history(limit: int = 100) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_trade_row_to_dict(r) for r in rows]


def trade_stats() -> dict:
    with _conn() as conn:
        resolved = conn.execute("SELECT * FROM trades WHERE status IN ('won','lost')").fetchall()

    resolved = [_trade_row_to_dict(r) for r in resolved]
    total = len(resolved)
    wins = [t for t in resolved if t["status"] == "won"]
    losses = [t for t in resolved if t["status"] == "lost"]
    win_rate = (len(wins) / total * 100) if total else None
    avg_pnl = (sum(t["pnl"] or 0 for t in resolved) / total) if total else None

    by_strategy: dict[str, dict] = {}
    for t in resolved:
        s = by_strategy.setdefault(t["strategy"], {"total": 0, "wins": 0})
        s["total"] += 1
        if t["status"] == "won":
            s["wins"] += 1
    for s in by_strategy.values():
        s["win_rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else None

    return {
        "total_resolved": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "avg_pnl": round(avg_pnl, 2) if avg_pnl is not None else None,
        "by_strategy": by_strategy,
    }


def _trade_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    signals_json = d.pop("signals_json", None)
    d["signals"] = json.loads(signals_json) if signals_json else []
    rationale_json = d.pop("rationale_json", None)
    d["rationale"] = json.loads(rationale_json) if rationale_json else []
    reason_tags_json = d.pop("reason_tags_json", None)
    d["reason_tags"] = json.loads(reason_tags_json) if reason_tags_json else []
    return d
