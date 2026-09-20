import pandas as pd
import pytest

from app.engine import indicators as ind


def _bars(closes, volumes=None, highs=None, lows=None):
    n = len(closes)
    volumes = volumes or [1000] * n
    highs = highs or [c + 0.5 for c in closes]
    lows = lows or [c - 0.5 for c in closes]
    idx = pd.date_range("2026-01-05 09:30", periods=n, freq="1min", tz="America/New_York")
    return pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": volumes}, index=idx)


def test_vwap_between_high_and_low_on_flat_series():
    df = _bars([100] * 10)
    vw = ind.vwap(df)
    assert vw.iloc[-1] == pytest.approx(100, abs=0.01)


def test_ema_reacts_faster_with_shorter_span():
    closes = [100] * 20 + [110] * 5
    df = _bars(closes)
    ema9 = ind.ema(df["Close"], 9)
    ema20 = ind.ema(df["Close"], 20)
    assert ema9.iloc[-1] > ema20.iloc[-1]


def test_rsi_rises_on_sustained_uptrend():
    closes = [100 + i for i in range(30)]
    df = _bars(closes)
    rsi = ind.rsi(df["Close"], 14)
    assert rsi.iloc[-1] > 70


def test_rsi_falls_on_sustained_downtrend():
    closes = [130 - i for i in range(30)]
    df = _bars(closes)
    rsi = ind.rsi(df["Close"], 14)
    assert rsi.iloc[-1] < 30


def test_atr_nonnegative():
    closes = [100, 101, 99, 102, 98, 103, 97, 104, 96, 105, 95, 106, 94, 107, 93]
    df = _bars(closes)
    atr = ind.atr(df, 14)
    assert (atr.dropna() >= 0).all()


def test_bollinger_bands_bracket_price_in_flat_market():
    df = _bars([100] * 25)
    upper, mid, lower = ind.bollinger_bands(df["Close"], 20, 2)
    assert lower.iloc[-1] <= mid.iloc[-1] <= upper.iloc[-1]


def test_volume_zscore_flags_spike():
    volumes = [1000] * 20 + [10000]
    df = _bars([100] * 21, volumes=volumes)
    z = ind.volume_zscore(df, 20)
    assert z.iloc[-1] > 2


def test_opening_range_none_before_enough_bars():
    df = _bars([100] * 5)
    assert ind.opening_range(df, 15) is None


def test_opening_range_computed_after_enough_bars():
    closes = [100, 101, 99, 102, 98] + [100] * 15
    df = _bars(closes)
    rng = ind.opening_range(df, 15)
    assert rng is not None
    high, low = rng
    assert high >= low
