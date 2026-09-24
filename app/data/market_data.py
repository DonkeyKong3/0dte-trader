"""Free-data market layer.

We trade signals off SPY (free, liquid, near-real-time via Yahoo Finance)
and translate them into SPX spread recommendations by scaling strikes with
the *live* SPX/SPY ratio (it drifts, so we never hardcode ~10x).

IMPORTANT: Yahoo Finance has no real-time SLA. Every payload here carries an
`as_of` timestamp so the caller/UI can show data staleness explicitly.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass, field

import pandas as pd
import yfinance as yf

from config import PROXY_TICKER, TARGET_TICKER, TZ

log = logging.getLogger(__name__)


@dataclass
class Quote:
    price: float
    as_of: dt.datetime


@dataclass
class OptionLeg:
    strike: float
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    implied_vol: float | None

    @property
    def mid(self) -> float:
        if self.bid and self.ask and self.ask >= self.bid:
            return round((self.bid + self.ask) / 2, 4)
        return self.last


@dataclass
class OptionsChain:
    expiration: str
    underlying_price: float
    calls: list[OptionLeg] = field(default_factory=list)
    puts: list[OptionLeg] = field(default_factory=list)


def _now() -> dt.datetime:
    return dt.datetime.now(TZ)


def get_intraday_bars(ticker: str = PROXY_TICKER, period: str = "1d", interval: str = "1m") -> pd.DataFrame:
    """1-minute OHLCV bars for the current session. Empty DataFrame on failure."""
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, prepost=False)
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df.index = df.index.tz_convert(TZ) if df.index.tz is not None else df.index.tz_localize(TZ)
    return df


def get_last_quote(ticker: str) -> Quote | None:
    df = get_intraday_bars(ticker, period="1d", interval="1m")
    if df.empty:
        return None
    last = df.iloc[-1]
    return Quote(price=float(last["Close"]), as_of=df.index[-1].to_pydatetime())


def get_spx_spy_ratio() -> float | None:
    """Live SPX/SPY ratio, computed fresh (it drifts, roughly 10.5-10.9)."""
    spx = get_last_quote(TARGET_TICKER)
    spy = get_last_quote(PROXY_TICKER)
    if not spx or not spy or spy.price == 0:
        return None
    return spx.price / spy.price


def _todays_expiration(ticker: yf.Ticker) -> str | None:
    today = _now().strftime("%Y-%m-%d")
    try:
        options = ticker.options
    except Exception:
        return None
    if today in options:
        return today
    return None


def _safe_float(value, default: float = 0.0) -> float:
    """`row.get(...) or default` silently mishandles NaN (NaN is truthy in
    Python, so `NaN or 0.0` evaluates to NaN, not 0.0) -- illiquid/newly
    listed contracts report NaN volume/OI/bid/ask constantly, so this must
    be an explicit NaN check, not a truthiness check."""
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except TypeError:
        pass
    return float(value)


def _safe_int(value, default: int = 0) -> int:
    return int(_safe_float(value, default))


def _to_legs(df: pd.DataFrame) -> list[OptionLeg]:
    legs = []
    for _, row in df.iterrows():
        legs.append(
            OptionLeg(
                strike=_safe_float(row.get("strike")),
                bid=_safe_float(row.get("bid")),
                ask=_safe_float(row.get("ask")),
                last=_safe_float(row.get("lastPrice")),
                volume=_safe_int(row.get("volume")),
                open_interest=_safe_int(row.get("openInterest")),
                implied_vol=float(row["impliedVolatility"]) if pd.notna(row.get("impliedVolatility")) else None,
            )
        )
    return legs


def _fetch_chain(ticker_symbol: str, expiration: str, underlying_price: float) -> OptionsChain | None:
    try:
        chain = yf.Ticker(ticker_symbol).option_chain(expiration)
        return OptionsChain(
            expiration=expiration,
            underlying_price=underlying_price,
            calls=_to_legs(chain.calls),
            puts=_to_legs(chain.puts),
        )
    except Exception:
        log.exception("Failed to fetch/parse options chain for %s %s", ticker_symbol, expiration)
        return None


def get_0dte_options_chain(ticker_symbol: str = PROXY_TICKER) -> OptionsChain | None:
    """Today's expiration chain for the proxy ticker, or None if SPY has no
    same-day expiration today (SPY trades M/W/F 0DTE-eligible expirations;
    Tue/Thu are the gap days -> caller should treat missing chain as
    'no 0DTE signal available' rather than guessing)."""
    t = yf.Ticker(ticker_symbol)
    expiration = _todays_expiration(t)
    if not expiration:
        return None
    quote = get_last_quote(ticker_symbol)
    underlying_price = quote.price if quote else float("nan")
    return _fetch_chain(ticker_symbol, expiration, underlying_price)


def get_nearest_expiration_options_chain(ticker_symbol: str = PROXY_TICKER) -> OptionsChain | None:
    """The soonest available expiration, regardless of whether it's today --
    used only for the demo/preview pipeline (market closed, or a Tue/Thu gap
    day) so the mechanics can be sanity-checked against real quotes. Not a
    substitute for a same-day 0DTE chain."""
    t = yf.Ticker(ticker_symbol)
    try:
        options = t.options
    except Exception:
        return None
    if not options:
        return None
    quote = get_last_quote(ticker_symbol)
    underlying_price = quote.price if quote else float("nan")
    return _fetch_chain(ticker_symbol, options[0], underlying_price)


def get_most_recent_session_bars(ticker: str = PROXY_TICKER) -> pd.DataFrame:
    """1-minute bars for the most recently completed trading session --
    works even when the market is currently closed (weekend/after-hours),
    unlike get_intraday_bars which only has today's (possibly empty) bars.
    Used by the demo/preview pipeline."""
    try:
        df = yf.Ticker(ticker).history(period="5d", interval="1m", prepost=False)
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df.index = df.index.tz_convert(TZ) if df.index.tz is not None else df.index.tz_localize(TZ)
    last_date = df.index[-1].date()
    return df[df.index.date == last_date]


# --- Black-Scholes IV/greeks fallback (yfinance's impliedVolatility field can
# be stale intraday, so we recompute from live mid price when possible). ---

def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_price(spot: float, strike: float, t_years: float, vol: float, rate: float, is_call: bool) -> float:
    if t_years <= 0 or vol <= 0:
        intrinsic = max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)
        return intrinsic
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t_years) / (vol * math.sqrt(t_years))
    d2 = d1 - vol * math.sqrt(t_years)
    if is_call:
        return spot * _norm_cdf(d1) - strike * math.exp(-rate * t_years) * _norm_cdf(d2)
    return strike * math.exp(-rate * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_vol_from_price(
    market_price: float, spot: float, strike: float, t_years: float, is_call: bool, rate: float = 0.05
) -> float | None:
    """Bisection solve for IV. Returns None if it can't converge (bad quote)."""
    if market_price <= 0 or t_years <= 0 or spot <= 0 or strike <= 0:
        return None
    lo, hi = 1e-4, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        price = bs_price(spot, strike, t_years, mid, rate, is_call)
        if price > market_price:
            hi = mid
        else:
            lo = mid
    result = (lo + hi) / 2
    return result if 1e-3 < result < 4.9 else None


def delta(spot: float, strike: float, t_years: float, vol: float, rate: float, is_call: bool) -> float | None:
    if t_years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return None
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t_years) / (vol * math.sqrt(t_years))
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1


# Floor on the time input to the IV solve specifically (NOT on the real
# t_years used elsewhere -- delta, expected-move scaling, force-close
# timing). As real time-to-expiry approaches zero, BS price becomes
# extremely sensitive to vol, so any small residual between a live quote
# and the current spot (a stale tick, a one-cent bid/ask spread, tick-size
# rounding) forces the solver to attribute it all to vol -- confirmed in
# production: expected-move estimates climbed from a realistic ~0.13% at
# midday to ~0.78% (implying >100% annualized IV) in the last minute of
# the session, a numerical artifact of solving IV that close to expiry,
# not a real market condition. Clamping the solve's time input to at
# least 15 minutes damps that sensitivity by ~10x while still using the
# option's real, current-day price.
_MIN_T_YEARS_FOR_IV_SOLVE = (15 * 60) / (365 * 24 * 3600)


def leg_effective_iv(leg: OptionLeg, spot: float, t_years: float, is_call: bool, rate: float = 0.05) -> float | None:
    """The IV to actually use for a leg: prefers solving from its live
    bid/ask mid price (internally consistent with the fresh quote we just
    pulled) over the chain's raw `impliedVolatility` field.

    Confirmed in production on an ordinary SPY session: the raw field was
    reporting ATM IV around 2-5% annualized, when realistic SPY IV is
    rarely below ~10% -- a same-day options data-quality issue (Yahoo's
    IV is frequently stale/unstable for thin, near-expiry 0DTE contracts),
    not a market condition. That silently capped every magnitude-dependent
    calculation (expected move, delta-based strike selection, IV skew)
    far below where it should be. Falls back to the raw field only when
    there's no usable quote to solve from."""
    if leg.mid > 0 and t_years and t_years > 0:
        solve_t_years = max(t_years, _MIN_T_YEARS_FOR_IV_SOLVE)
        solved = implied_vol_from_price(leg.mid, spot, leg.strike, solve_t_years, is_call, rate)
        if solved:
            return solved
    return leg.implied_vol if leg.implied_vol and leg.implied_vol > 0 else None
