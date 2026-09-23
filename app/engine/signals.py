"""Individual signal generators. Each returns a SignalResult with a
direction ("bullish" | "bearish" | "neutral") and a strength 0-100 (how
strongly the evidence supports that direction) plus a human-readable
detail string for the dashboard/audit trail.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.data.market_data import OptionsChain, leg_effective_iv
from app.engine import indicators as ind


@dataclass
class SignalResult:
    name: str
    direction: str  # "bullish" | "bearish" | "neutral"
    strength: float  # 0-100
    detail: str


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def trend_signal(df: pd.DataFrame) -> SignalResult:
    if len(df) < 20:
        return SignalResult("trend", "neutral", 0, "Not enough bars yet")

    close = df["Close"]
    ema9 = ind.ema(close, 9)
    ema20 = ind.ema(close, 20)
    vw = ind.vwap(df)

    price = close.iloc[-1]
    e9, e20, v = ema9.iloc[-1], ema20.iloc[-1], vw.iloc[-1]

    bullish_votes = sum([price > v, e9 > e20, price > e9])
    bearish_votes = sum([price < v, e9 < e20, price < e9])

    ema_gap_pct = abs(e9 - e20) / e20 * 100 if e20 else 0
    strength = _clip(30 + ema_gap_pct * 400)

    if bullish_votes >= 2 and bullish_votes > bearish_votes:
        return SignalResult("trend", "bullish", strength, f"Price above VWAP/EMA9>EMA20 ({bullish_votes}/3 aligned)")
    if bearish_votes >= 2 and bearish_votes > bullish_votes:
        return SignalResult("trend", "bearish", strength, f"Price below VWAP/EMA9<EMA20 ({bearish_votes}/3 aligned)")
    return SignalResult("trend", "neutral", 20, "No clear VWAP/EMA alignment")


def momentum_signal(df: pd.DataFrame) -> SignalResult:
    if len(df) < 15:
        return SignalResult("momentum", "neutral", 0, "Not enough bars yet")

    rsi = ind.rsi(df["Close"], 14).iloc[-1]
    if pd.isna(rsi):
        return SignalResult("momentum", "neutral", 0, "RSI not available yet")

    distance = abs(rsi - 50)
    strength = _clip(distance * 2.2)

    if rsi >= 58:
        return SignalResult("momentum", "bullish", strength, f"RSI {rsi:.1f} (momentum up)")
    if rsi <= 42:
        return SignalResult("momentum", "bearish", strength, f"RSI {rsi:.1f} (momentum down)")
    return SignalResult("momentum", "neutral", _clip(distance * 1.5), f"RSI {rsi:.1f} (no clear momentum)")


def volume_signal(df: pd.DataFrame) -> SignalResult:
    if len(df) < 21:
        return SignalResult("volume", "neutral", 0, "Not enough bars yet")

    z = ind.volume_zscore(df, 20).iloc[-1]
    price_change = df["Close"].iloc[-1] - df["Close"].iloc[-2]
    if pd.isna(z):
        return SignalResult("volume", "neutral", 0, "Volume z-score not available yet")

    if z < 0.5:
        return SignalResult("volume", "neutral", _clip(z * 20), f"Volume unremarkable (z={z:.2f})")

    strength = _clip(30 + z * 25)
    if price_change > 0:
        return SignalResult("volume", "bullish", strength, f"Above-average volume (z={z:.2f}) confirming up move")
    if price_change < 0:
        return SignalResult("volume", "bearish", strength, f"Above-average volume (z={z:.2f}) confirming down move")
    return SignalResult("volume", "neutral", strength * 0.5, f"High volume (z={z:.2f}) but flat price")


def opening_range_signal(df: pd.DataFrame, minutes: int) -> SignalResult:
    rng = ind.opening_range(df, minutes)
    if rng is None:
        return SignalResult("opening_range", "neutral", 0, "Opening range not established yet")

    high, low = rng
    price = df["Close"].iloc[-1]
    range_size = high - low
    if range_size <= 0:
        return SignalResult("opening_range", "neutral", 0, "Degenerate opening range")

    if price > high:
        breakout_pct = (price - high) / range_size * 100
        return SignalResult("opening_range", "bullish", _clip(40 + breakout_pct), f"Broke above opening range high {high:.2f}")
    if price < low:
        breakdown_pct = (low - price) / range_size * 100
        return SignalResult("opening_range", "bearish", _clip(40 + breakdown_pct), f"Broke below opening range low {low:.2f}")
    return SignalResult("opening_range", "neutral", 10, f"Still inside opening range [{low:.2f}, {high:.2f}]")


def iv_skew_signal(chain: OptionsChain | None, spot: float | None = None, t_years: float | None = None) -> SignalResult:
    """Compares near-the-money put vs call implied vol. Richer puts (put
    skew) reflect hedging/fear demand -- a classic tell for elevated
    downside risk pricing, which we treat as a mildly bearish tilt and as
    a reason credit-spread premium is richer than usual.

    IV here is solved from each leg's live mid price (leg_effective_iv),
    not read directly from the chain's raw impliedVolatility field, which
    is frequently stale/unreliable for thin, near-expiry 0DTE contracts --
    confirmed in production reporting SPY ATM IV around 2-5% on an
    ordinary session. `spot`/`t_years` are required to solve it; without
    them this falls back to the raw field only.
    """
    if chain is None or not chain.calls or not chain.puts or chain.underlying_price != chain.underlying_price:
        return SignalResult("iv_skew", "neutral", 0, "Options chain unavailable")

    spot = spot if spot is not None else chain.underlying_price

    def nearest_atm(legs, is_call):
        candidates = [leg for leg in legs if leg.mid > 0 or (leg.implied_vol and leg.implied_vol > 0)]
        if not candidates:
            return None, None
        leg = min(candidates, key=lambda leg: abs(leg.strike - spot))
        iv = leg_effective_iv(leg, spot, t_years, is_call) if t_years else (leg.implied_vol or None)
        return leg, iv

    atm_call, call_iv = nearest_atm(chain.calls, True)
    atm_put, put_iv = nearest_atm(chain.puts, False)
    if not call_iv or not put_iv:
        return SignalResult("iv_skew", "neutral", 0, "No usable IV quotes near the money")

    skew = put_iv - call_iv  # positive => puts richer
    strength = _clip(abs(skew) * 800)

    if skew > 0.01:
        return SignalResult("iv_skew", "bearish", strength, f"Put skew {skew:.3f} (downside hedging demand)")
    if skew < -0.01:
        return SignalResult("iv_skew", "bullish", strength, f"Call skew {skew:.3f} (upside demand / low fear)")
    return SignalResult("iv_skew", "neutral", 15, f"Flat skew ({skew:.3f})")
