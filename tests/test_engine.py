import datetime as dt

import pandas as pd
import pytest

from config import NO_NEW_TRADES_AFTER, TZ
from app.data.market_data import OptionsChain
from app.engine import engine as eng


def _full_session_bars(session_date: dt.date) -> pd.DataFrame:
    """A full 9:30-16:00 ET session of 1-minute bars, flat price/volume."""
    start = dt.datetime.combine(session_date, dt.time(9, 30), tzinfo=TZ)
    idx = pd.date_range(start, periods=390, freq="1min", tz=TZ)  # 9:30 -> 15:59
    return pd.DataFrame(
        {"Open": 500.0, "High": 500.5, "Low": 499.5, "Close": 500.0, "Volume": 1000},
        index=idx,
    )


def test_demo_cycle_evaluates_before_cutoff_not_at_last_bar(monkeypatch):
    session_date = dt.date(2026, 1, 6)  # an arbitrary Tuesday, no curated events
    bars = _full_session_bars(session_date)

    monkeypatch.setattr(eng, "get_most_recent_session_bars", lambda ticker: bars)
    monkeypatch.setattr(eng, "get_nearest_expiration_options_chain", lambda ticker: None)
    monkeypatch.setattr(eng, "get_spx_spy_ratio", lambda: None)

    result = eng.run_demo_cycle()

    assert result["demo"] is True
    assert result["session_date"] == session_date.isoformat()
    expected_eval_time = f"{NO_NEW_TRADES_AFTER[0]:02d}:{NO_NEW_TRADES_AFTER[1] - 1:02d} ET"
    assert result["evaluated_at"] == expected_eval_time
    # The old bug: evaluating at the session's last bar (~15:59) always hit
    # the cutoff rule regardless of signal quality. It must not appear here.
    assert not any("cutoff" in r.lower() for r in result["reasons"])


def test_demo_cycle_handles_no_historical_data(monkeypatch):
    monkeypatch.setattr(eng, "get_most_recent_session_bars", lambda ticker: pd.DataFrame())

    result = eng.run_demo_cycle()

    assert result["demo"] is True
    assert result["tradeable"] is False
    assert "No historical market data available" in result["reasons"]
