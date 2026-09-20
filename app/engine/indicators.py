"""Pure technical-indicator functions over an OHLCV DataFrame.

Expects columns: Open, High, Low, Close, Volume, indexed by tz-aware
datetime (ET). No I/O here -- keeps this layer unit-testable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def vwap(df: pd.DataFrame) -> pd.Series:
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_vol = df["Volume"].cumsum()
    cum_vol_price = (typical_price * df["Volume"]).cumsum()
    return cum_vol_price / cum_vol.replace(0, np.nan)


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    # avg_loss == 0: no down bars in the lookback -> maximally overbought (100),
    # unless avg_gain is also 0 (flat series), which is neutral (50).
    result = result.where(avg_loss != 0, np.where(avg_gain == 0, 50.0, 100.0))
    return result


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def volume_zscore(df: pd.DataFrame, period: int = 20) -> pd.Series:
    rolling_mean = df["Volume"].rolling(period).mean()
    rolling_std = df["Volume"].rolling(period).std()
    return (df["Volume"] - rolling_mean) / rolling_std.replace(0, np.nan)


def opening_range(df: pd.DataFrame, minutes: int) -> tuple[float, float] | None:
    """(high, low) of the first `minutes` bars of the session, or None if
    there isn't enough data yet."""
    if df.empty:
        return None
    session_start = df.index[0]
    window = df[df.index < session_start + pd.Timedelta(minutes=minutes)]
    if len(window) < minutes:
        return None
    return float(window["High"].max()), float(window["Low"].min())
