import datetime as dt
import sqlite3

from config import TZ
from app import db

_OLD_SIGNALS_SCHEMA = """
CREATE TABLE signals (
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


def test_init_db_adds_missing_columns_to_pre_existing_signals_table(tmp_path, monkeypatch):
    """Regression test: a signals.db created before the prediction feature
    shipped has a `signals` table with none of the predicted_*/realized_*
    columns. CREATE TABLE IF NOT EXISTS is a no-op on it, so record_cycle's
    INSERT (which always names those columns) used to fail with
    "no column named predicted_bucket" on every single call -- silently,
    since nothing in the request path surfaces that to the UI."""
    db_path = str(tmp_path / "old.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)

    # Simulate a pre-existing db with the old, narrower schema.
    conn = sqlite3.connect(db_path)
    conn.executescript(_OLD_SIGNALS_SCHEMA)
    conn.commit()
    conn.close()

    db.init_db()  # must migrate, not just no-op

    now = dt.datetime(2026, 1, 6, 10, 0, tzinfo=TZ)
    db.record_cycle(
        tradeable=False,
        direction="none",
        score=42.0,
        spy_price=500.0,
        spx_estimate=5000.0,
        card=None,
        reasons=[],
        predicted_bucket="small_up",
        predicted_confidence=38.5,
        predicted_net_score=38.5,
        prediction_resolve_by=now + dt.timedelta(minutes=30),
        now=now,
    )

    row = db.latest()
    assert row is not None
    assert row["predicted_bucket"] == "small_up"


def test_init_db_is_idempotent_on_fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "fresh.db"))
    db.init_db()
    db.init_db()  # must not error on a second call
    assert db.latest() is None
