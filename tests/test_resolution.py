import datetime as dt

import pytest

from config import CONFIDENCE_THRESHOLD, TZ
from app import db
from app.engine import resolution


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    yield


def _card(strategy="bull_put_credit", direction="bullish", credit=3.0, target=1.2, stop=4.5):
    return {
        "strategy": strategy,
        "direction": direction,
        "expiration": "2026-01-06",
        "short_strike": 4990,
        "long_strike": 4980,
        "call_short_strike": None,
        "call_long_strike": None,
        "put_short_strike": None,
        "put_long_strike": None,
        "width": 10,
        "est_credit_debit": credit,
        "max_profit": credit,
        "max_loss": 10 - credit,
        "profit_target_price": target,
        "stop_loss_price": stop,
        "confidence_score": 75.0,
        "rationale": ["test rationale"],
    }


class _Quote:
    def __init__(self, price):
        self.price = price


def test_pnl_credit_spread_profit_and_loss():
    trade = {"strategy": "bull_put_credit", "entry_price": 3.0}
    assert resolution._pnl(trade, 1.0) == pytest.approx(2.0)   # bought back cheaper than credit -> profit
    assert resolution._pnl(trade, 5.0) == pytest.approx(-2.0)  # cost more to close -> loss


def test_pnl_debit_spread_profit_and_loss():
    trade = {"strategy": "bull_call_debit", "entry_price": 2.0}
    assert resolution._pnl(trade, 3.5) == pytest.approx(1.5)
    assert resolution._pnl(trade, 0.5) == pytest.approx(-1.5)


def test_tags_directional_wrong_when_price_moved_opposite_prediction():
    trade = {"direction": "bullish", "entry_spy_price": 500.0, "confidence_score": 90.0}
    tags = resolution._tag_loss_reasons(trade, dt.datetime(2026, 1, 6, 15, 0, tzinfo=TZ), 495.0)
    assert any("Directional call wrong" in t for t in tags)


def test_tags_direction_right_but_stopped_out_when_price_moved_with_prediction():
    trade = {"direction": "bullish", "entry_spy_price": 500.0, "confidence_score": 90.0}
    tags = resolution._tag_loss_reasons(trade, dt.datetime(2026, 1, 6, 15, 0, tzinfo=TZ), 500.5)
    assert any("Direction was right" in t for t in tags)
    assert not any("Directional call wrong" in t for t in tags)


def test_tags_borderline_confidence():
    trade = {"direction": "bullish", "entry_spy_price": 500.0, "confidence_score": CONFIDENCE_THRESHOLD + 1}
    tags = resolution._tag_loss_reasons(trade, dt.datetime(2026, 1, 6, 15, 0, tzinfo=TZ), 505.0)
    assert any("borderline confidence" in t for t in tags)


def test_check_open_trades_resolves_on_profit_target(monkeypatch):
    now = dt.datetime(2026, 1, 6, 10, 0, tzinfo=TZ)
    force_close = now.replace(hour=15, minute=45)
    db.open_trade(_card(), [], force_close, now, entry_spy_price=500.0)

    monkeypatch.setattr(resolution, "get_0dte_options_chain", lambda ticker: object())
    monkeypatch.setattr(resolution, "get_spx_spy_ratio", lambda: 10.0)
    monkeypatch.setattr(resolution, "get_last_quote", lambda ticker: _Quote(501.0))
    monkeypatch.setattr(resolution, "reprice_trade", lambda trade, chain, ratio: 1.0)  # below target 1.2

    resolution.check_open_trades(now + dt.timedelta(minutes=5))

    trade = db.trades_history(10)[0]
    assert trade["status"] == "won"
    assert trade["exit_reason_code"] == "profit_target"
    assert trade["pnl"] == pytest.approx(2.0)
    assert db.open_trades() == []


def test_check_open_trades_resolves_on_stop_loss_and_tags_reason(monkeypatch):
    now = dt.datetime(2026, 1, 6, 10, 0, tzinfo=TZ)
    force_close = now.replace(hour=15, minute=45)
    db.open_trade(_card(direction="bullish"), [], force_close, now, entry_spy_price=500.0)

    monkeypatch.setattr(resolution, "get_0dte_options_chain", lambda ticker: object())
    monkeypatch.setattr(resolution, "get_spx_spy_ratio", lambda: 10.0)
    monkeypatch.setattr(resolution, "get_last_quote", lambda ticker: _Quote(495.0))  # moved against bullish call
    monkeypatch.setattr(resolution, "reprice_trade", lambda trade, chain, ratio: 5.0)  # above stop 4.5

    resolution.check_open_trades(now + dt.timedelta(minutes=5))

    trade = db.trades_history(10)[0]
    assert trade["status"] == "lost"
    assert trade["exit_reason_code"] == "stop_loss"
    assert trade["pnl"] == pytest.approx(-2.0)
    assert any("Directional call wrong" in t for t in trade["reason_tags"])


def test_check_open_trades_force_closes_at_cutoff_between_target_and_stop(monkeypatch):
    now = dt.datetime(2026, 1, 6, 10, 0, tzinfo=TZ)
    force_close = now.replace(hour=15, minute=45)
    db.open_trade(_card(), [], force_close, now, entry_spy_price=500.0)

    monkeypatch.setattr(resolution, "get_0dte_options_chain", lambda ticker: object())
    monkeypatch.setattr(resolution, "get_spx_spy_ratio", lambda: 10.0)
    monkeypatch.setattr(resolution, "get_last_quote", lambda ticker: _Quote(502.0))
    monkeypatch.setattr(resolution, "reprice_trade", lambda trade, chain, ratio: 2.0)  # between target 1.2 and stop 4.5

    resolution.check_open_trades(force_close + dt.timedelta(minutes=1))

    trade = db.trades_history(10)[0]
    assert trade["exit_reason_code"] == "time_cutoff"
    assert trade["status"] in ("won", "lost")


def test_check_open_trades_does_not_resolve_before_cutoff_or_thresholds(monkeypatch):
    now = dt.datetime(2026, 1, 6, 10, 0, tzinfo=TZ)
    force_close = now.replace(hour=15, minute=45)
    db.open_trade(_card(), [], force_close, now, entry_spy_price=500.0)

    monkeypatch.setattr(resolution, "get_0dte_options_chain", lambda ticker: object())
    monkeypatch.setattr(resolution, "get_spx_spy_ratio", lambda: 10.0)
    monkeypatch.setattr(resolution, "get_last_quote", lambda ticker: _Quote(500.5))
    monkeypatch.setattr(resolution, "reprice_trade", lambda trade, chain, ratio: 2.0)  # between target/stop

    resolution.check_open_trades(now + dt.timedelta(minutes=5))  # well before force_close

    assert len(db.open_trades()) == 1
    assert db.trades_history(10)[0]["status"] == "open"


def test_check_open_trades_marks_unresolved_when_no_price_at_cutoff(monkeypatch):
    now = dt.datetime(2026, 1, 6, 10, 0, tzinfo=TZ)
    force_close = now.replace(hour=15, minute=45)
    db.open_trade(_card(), [], force_close, now, entry_spy_price=500.0)

    monkeypatch.setattr(resolution, "get_0dte_options_chain", lambda ticker: None)
    monkeypatch.setattr(resolution, "get_spx_spy_ratio", lambda: None)
    monkeypatch.setattr(resolution, "get_last_quote", lambda ticker: None)
    monkeypatch.setattr(resolution, "reprice_trade", lambda trade, chain, ratio: None)

    resolution.check_open_trades(force_close + dt.timedelta(minutes=1))

    trade = db.trades_history(10)[0]
    assert trade["status"] == "unresolved"
    assert trade["exit_reason_code"] == "unresolved"
