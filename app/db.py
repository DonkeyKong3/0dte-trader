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
    d["signals"] = json.loads(d.pop("signals_json")) if d.get("signals_json") else []
    d["rationale"] = json.loads(d.pop("rationale_json")) if d.get("rationale_json") else []
    d["reason_tags"] = json.loads(d.pop("reason_tags_json")) if d.get("reason_tags_json") else []
    return d
