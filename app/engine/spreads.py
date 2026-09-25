"""Turns a confidence verdict + market data into a concrete SPX spread
recommendation: strategy, strikes, width, estimated credit/debit, and the
exact rules for closing it (profit target, stop loss, hard time cutoff).

All strike/premium figures are ESTIMATES derived by scaling the SPY 0DTE
chain to SPX-dollar terms via the live SPX/SPY ratio -- they are for sizing
and direction, not an execution-grade SPX quote. Always confirm against a
live SPX chain before entering.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from config import (
    CONDOR_MAX_SIGNAL_STRENGTH,
    CONDOR_MIN_IV,
    CONDOR_RANGE_SCORE_THRESHOLD,
    CONDOR_SPREAD_WIDTH_SPX,
    DEBIT_PROFIT_TARGET_FRACTION,
    DEBIT_STOP_LOSS_FRACTION,
    DEFAULT_SPREAD_WIDTH_SPX,
    FORCE_CLOSE_BY,
    PROFIT_TARGET_FRACTION,
    STOP_LOSS_MULTIPLE,
    TARGET_SHORT_DELTA,
    TZ,
)
from app.data.market_data import OptionLeg, OptionsChain, delta, leg_effective_iv
from app.engine.confidence import ConfidenceVerdict
from app.engine.signals import SignalResult

RISK_FREE_RATE = 0.05


def _round_to(value: float, base: float) -> float:
    return round(value / base) * base


def time_to_expiry_years(now: dt.datetime, expiration: str) -> float:
    """Years until 4pm ET on `expiration` (YYYY-MM-DD). Normally that's
    today (live 0DTE), but the demo/preview pipeline can pass a chain whose
    nearest expiration is a different, later date -- so this is computed
    from the chain's actual expiration rather than assumed to be `now`'s
    own date. Public so engine.py can reuse it for the prediction's IV
    lookup without recomputing the same thing twice."""
    exp_date = dt.datetime.strptime(expiration, "%Y-%m-%d").date()
    close = dt.datetime.combine(exp_date, dt.time(16, 0), tzinfo=now.tzinfo)
    seconds_left = max((close - now).total_seconds(), 60)  # floor so BS math stays sane
    return seconds_left / (365 * 24 * 3600)


def _leg_delta(leg: OptionLeg, spot: float, t_years: float, is_call: bool) -> float | None:
    iv = leg_effective_iv(leg, spot, t_years, is_call, RISK_FREE_RATE)
    if not iv:
        return None
    return delta(spot, leg.strike, t_years, iv, RISK_FREE_RATE, is_call)


def _pick_short_leg(legs: list[OptionLeg], spot: float, t_years: float, is_call: bool, target_delta: float) -> OptionLeg | None:
    best, best_diff = None, None
    for leg in legs:
        d = _leg_delta(leg, spot, t_years, is_call)
        if d is None:
            continue
        diff = abs(abs(d) - target_delta)
        if best_diff is None or diff < best_diff:
            best, best_diff = leg, diff
    return best


def _find_leg_near_strike(legs: list[OptionLeg], strike: float) -> OptionLeg | None:
    if not legs:
        return None
    return min(legs, key=lambda leg: abs(leg.strike - strike))


def atm_iv(chain: OptionsChain, spot: float, t_years: float) -> float | None:
    """Average of the nearest-the-money call/put effective IV (solved from
    live price, not the chain's raw field -- see leg_effective_iv). Public
    so prediction.py can reuse it for an IV-implied expected-move estimate,
    independent of the directional signal score. `t_years` is the OPTION's
    actual time-to-expiry (needed to solve IV), not the prediction horizon
    the caller may separately be projecting over."""
    call = _find_leg_near_strike(chain.calls, spot)
    put = _find_leg_near_strike(chain.puts, spot)
    ivs = [
        leg_effective_iv(leg, spot, t_years, is_call)
        for leg, is_call in ((call, True), (put, False))
        if leg
    ]
    ivs = [iv for iv in ivs if iv]
    if not ivs:
        return None
    return sum(ivs) / len(ivs)


def reprice_trade(trade: dict, chain: OptionsChain, ratio: float | None) -> float | None:
    """Re-prices an already-opened tracked trade's exact strikes against a
    fresh chain snapshot, in the same units as entry_price/profit_target_
    price/stop_loss_price so the resolution engine can compare directly:
    for credit spreads and the condor this is the cost to buy back; for
    debit spreads it's the spread's current resale value. Returns None if
    the chain doesn't have usable quotes for those strikes right now (e.g.
    a momentary bad tick) -- caller should just retry next cycle rather
    than resolve on missing data.
    """
    if chain is None or ratio is None:
        return None

    strategy = trade["strategy"]

    def leg_mid_at_spx_strike(legs: list[OptionLeg], spx_strike: float | None) -> float | None:
        if spx_strike is None:
            return None
        leg = _find_leg_near_strike(legs, spx_strike / ratio)
        return leg.mid if leg else None

    if strategy == "iron_condor":
        call_short = leg_mid_at_spx_strike(chain.calls, trade["call_short_strike"])
        call_long = leg_mid_at_spx_strike(chain.calls, trade["call_long_strike"])
        put_short = leg_mid_at_spx_strike(chain.puts, trade["put_short_strike"])
        put_long = leg_mid_at_spx_strike(chain.puts, trade["put_long_strike"])
        if None in (call_short, call_long, put_short, put_long):
            return None
        cost_to_close_spy = (call_short - call_long) + (put_short - put_long)
        return max(cost_to_close_spy * ratio, 0.0)

    is_call = strategy in ("bear_call_credit", "bull_call_debit")
    legs = chain.calls if is_call else chain.puts
    short_mid = leg_mid_at_spx_strike(legs, trade["short_strike"])
    long_mid = leg_mid_at_spx_strike(legs, trade["long_strike"])
    if short_mid is None or long_mid is None:
        return None

    if strategy in ("bull_put_credit", "bear_call_credit"):
        return max((short_mid - long_mid) * ratio, 0.0)
    return max((long_mid - short_mid) * ratio, 0.0)  # bull_call_debit, bear_put_debit


def _signal(signals: list[SignalResult], name: str) -> SignalResult | None:
    return next((s for s in signals if s.name == name), None)


def range_bound_score(signals: list[SignalResult]) -> float:
    """High when trend/momentum/volume/opening-range all read quiet/neutral
    -- the condition an iron condor wants, distinct from directional
    confidence."""
    from config import SIGNAL_WEIGHTS

    total_weight = sum(SIGNAL_WEIGHTS.get(n, 0) for n in ("trend", "momentum", "volume", "opening_range"))
    if total_weight == 0:
        return 0.0
    score = 0.0
    for name in ("trend", "momentum", "volume", "opening_range"):
        sig = _signal(signals, name)
        weight = SIGNAL_WEIGHTS.get(name, 0)
        if sig is None:
            continue
        quietness = 100.0 if sig.direction == "neutral" else max(0.0, 100 - sig.strength)
        score += weight * (quietness / 100)
    return score / total_weight * 100


def max_directional_signal_strength(signals: list[SignalResult]) -> float:
    """Highest strength among the range-relevant signals that are actually
    directional (a neutral reading already counts as full quietness in
    range_bound_score, so it's excluded here). A WEIGHTED AVERAGE can still
    clear the range_bound_score threshold even when one signal is genuinely
    strong, as long as the others are quiet enough to average it out -- this
    is a separate, per-signal check so a single strong signal can veto
    condor eligibility on its own, regardless of the average."""
    relevant = ("trend", "momentum", "volume", "opening_range")
    strengths = [s.strength for s in signals if s.name in relevant and s.direction != "neutral"]
    return max(strengths) if strengths else 0.0


@dataclass
class TradeCard:
    strategy: str  # bull_put_credit | bear_call_credit | bull_call_debit | bear_put_debit | iron_condor
    direction: str  # bullish | bearish | neutral
    expiration: str
    short_strike: float | None
    long_strike: float | None
    call_short_strike: float | None = None
    call_long_strike: float | None = None
    put_short_strike: float | None = None
    put_long_strike: float | None = None
    width: float = DEFAULT_SPREAD_WIDTH_SPX
    est_credit_debit: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0
    profit_target_price: float = 0.0
    stop_loss_price: float = 0.0
    force_close_by: str = f"{FORCE_CLOSE_BY[0]:02d}:{FORCE_CLOSE_BY[1]:02d} ET"
    confidence_score: float = 0.0
    rationale: list[str] = field(default_factory=list)
    data_as_of: str = ""
    note: str = (
        "Strikes/premiums are estimates scaled from the SPY 0DTE chain via the live "
        "SPX/SPY ratio. Confirm against a live SPX quote before entering. Profit "
        "target and stop loss are TRIGGER levels, not guaranteed fills -- the paper "
        "tracker only re-checks prices once per 60-second poll, so the actual exit "
        "can land past the stated level in either direction (confirmed in "
        "production: realized stop-loss exits have run up to ~45% beyond the "
        "stated stop). A real broker order would behave differently."
    )


def _credit_spread_card(
    strategy: str,
    direction: str,
    short_leg: OptionLeg,
    ratio: float,
    is_call: bool,
    verdict: ConfidenceVerdict,
    chain: OptionsChain,
) -> TradeCard:
    width_spy = DEFAULT_SPREAD_WIDTH_SPX / ratio
    long_strike_spy = short_leg.strike + width_spy if is_call else short_leg.strike - width_spy
    long_leg = _find_leg_near_strike(chain.calls if is_call else chain.puts, long_strike_spy)

    short_strike_spx = _round_to(short_leg.strike * ratio, 5)
    long_strike_spx = _round_to(long_leg.strike * ratio, 5) if long_leg else (
        short_strike_spx + DEFAULT_SPREAD_WIDTH_SPX if is_call else short_strike_spx - DEFAULT_SPREAD_WIDTH_SPX
    )
    width = abs(short_strike_spx - long_strike_spx)

    credit_spy = short_leg.mid - (long_leg.mid if long_leg else 0.0)
    credit_spx = max(credit_spy * ratio, 0.01)
    max_profit = credit_spx
    max_loss = max(width - credit_spx, 0.01)

    return TradeCard(
        strategy=strategy,
        direction=direction,
        expiration=chain.expiration,
        short_strike=short_strike_spx,
        long_strike=long_strike_spx,
        width=width,
        est_credit_debit=round(credit_spx, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        profit_target_price=round(credit_spx * (1 - PROFIT_TARGET_FRACTION), 2),
        stop_loss_price=round(credit_spx * STOP_LOSS_MULTIPLE, 2),
        confidence_score=round(verdict.score, 1),
        rationale=[s.detail for s in verdict.signals if s.direction == direction],
    )


def _debit_spread_card(
    strategy: str,
    direction: str,
    ratio: float,
    is_call: bool,
    verdict: ConfidenceVerdict,
    chain: OptionsChain,
    spot: float,
) -> TradeCard:
    long_leg = _find_leg_near_strike(chain.calls if is_call else chain.puts, spot)
    if long_leg is None:
        raise ValueError("No ATM leg available for debit spread")
    width_spy = DEFAULT_SPREAD_WIDTH_SPX / ratio
    short_strike_spy = long_leg.strike + width_spy if is_call else long_leg.strike - width_spy
    short_leg = _find_leg_near_strike(chain.calls if is_call else chain.puts, short_strike_spy)

    long_strike_spx = _round_to(long_leg.strike * ratio, 5)
    short_strike_spx = _round_to(short_leg.strike * ratio, 5) if short_leg else (
        long_strike_spx + DEFAULT_SPREAD_WIDTH_SPX if is_call else long_strike_spx - DEFAULT_SPREAD_WIDTH_SPX
    )
    width = abs(short_strike_spx - long_strike_spx)

    debit_spy = long_leg.mid - (short_leg.mid if short_leg else 0.0)
    debit_spx = max(debit_spy * ratio, 0.01)
    max_loss = debit_spx
    max_profit = max(width - debit_spx, 0.01)

    return TradeCard(
        strategy=strategy,
        direction=direction,
        expiration=chain.expiration,
        short_strike=short_strike_spx,
        long_strike=long_strike_spx,
        width=width,
        est_credit_debit=round(debit_spx, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        profit_target_price=round(debit_spx * (1 + DEBIT_PROFIT_TARGET_FRACTION), 2),
        stop_loss_price=round(debit_spx * (1 - DEBIT_STOP_LOSS_FRACTION), 2),
        confidence_score=round(verdict.score, 1),
        rationale=[s.detail for s in verdict.signals if s.direction == direction],
    )


def _iron_condor_card(
    ratio: float,
    chain: OptionsChain,
    spot: float,
    t_years: float,
    range_score: float,
) -> TradeCard:
    short_call = _pick_short_leg(chain.calls, spot, t_years, True, TARGET_SHORT_DELTA)
    short_put = _pick_short_leg(chain.puts, spot, t_years, False, TARGET_SHORT_DELTA)
    if not short_call or not short_put:
        raise ValueError("No usable legs for iron condor")

    width_spy = CONDOR_SPREAD_WIDTH_SPX / ratio
    long_call = _find_leg_near_strike(chain.calls, short_call.strike + width_spy)
    long_put = _find_leg_near_strike(chain.puts, short_put.strike - width_spy)

    call_short_spx = _round_to(short_call.strike * ratio, 5)
    call_long_spx = _round_to(long_call.strike * ratio, 5) if long_call else call_short_spx + CONDOR_SPREAD_WIDTH_SPX
    put_short_spx = _round_to(short_put.strike * ratio, 5)
    put_long_spx = _round_to(long_put.strike * ratio, 5) if long_put else put_short_spx - CONDOR_SPREAD_WIDTH_SPX

    call_width = abs(call_long_spx - call_short_spx)
    put_width = abs(put_short_spx - put_long_spx)
    width = max(call_width, put_width)

    call_credit_spy = short_call.mid - (long_call.mid if long_call else 0.0)
    put_credit_spy = short_put.mid - (long_put.mid if long_put else 0.0)
    total_credit_spx = max((call_credit_spy + put_credit_spy) * ratio, 0.01)
    max_profit = total_credit_spx
    max_loss = max(width - total_credit_spx, 0.01)

    return TradeCard(
        strategy="iron_condor",
        direction="neutral",
        expiration=chain.expiration,
        short_strike=None,
        long_strike=None,
        call_short_strike=call_short_spx,
        call_long_strike=call_long_spx,
        put_short_strike=put_short_spx,
        put_long_strike=put_long_spx,
        width=width,
        est_credit_debit=round(total_credit_spx, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        profit_target_price=round(total_credit_spx * (1 - PROFIT_TARGET_FRACTION), 2),
        stop_loss_price=round(total_credit_spx * STOP_LOSS_MULTIPLE, 2),
        confidence_score=round(range_score, 1),
        rationale=[f"Range-bound score {range_score:.0f}/100 -- quiet trend/momentum/volume/opening-range readings"],
    )


def build_trade_card(
    verdict: ConfidenceVerdict,
    chain: OptionsChain | None,
    ratio: float | None,
    now: dt.datetime | None = None,
) -> TradeCard | None:
    """Returns None when there's nothing confident enough to suggest --
    including when the data we'd need (chain, ratio) simply isn't available.
    """
    now = now or dt.datetime.now(TZ)
    if chain is None or ratio is None or chain.underlying_price != chain.underlying_price:
        return None

    spot = chain.underlying_price
    t_years = time_to_expiry_years(now, chain.expiration)

    if verdict.tradeable and verdict.direction in ("bullish", "bearish"):
        momentum = _signal(verdict.signals, "momentum")
        volume = _signal(verdict.signals, "volume")
        breakout_confirmed = (
            momentum is not None
            and momentum.strength >= 60
            and volume is not None
            and volume.direction == verdict.direction
            and volume.strength >= 50
        )
        try:
            if verdict.direction == "bullish":
                if breakout_confirmed:
                    card = _debit_spread_card("bull_call_debit", "bullish", ratio, True, verdict, chain, spot)
                else:
                    short_leg = _pick_short_leg(chain.puts, spot, t_years, False, TARGET_SHORT_DELTA)
                    if not short_leg:
                        return None
                    card = _credit_spread_card("bull_put_credit", "bullish", short_leg, ratio, False, verdict, chain)
            else:
                if breakout_confirmed:
                    card = _debit_spread_card("bear_put_debit", "bearish", ratio, False, verdict, chain, spot)
                else:
                    short_leg = _pick_short_leg(chain.calls, spot, t_years, True, TARGET_SHORT_DELTA)
                    if not short_leg:
                        return None
                    card = _credit_spread_card("bear_call_credit", "bearish", short_leg, ratio, True, verdict, chain)
        except ValueError:
            return None
        card.data_as_of = now.isoformat()
        return card

    # Not directionally tradeable -- check for an iron-condor (range-bound) setup.
    range_score = range_bound_score(verdict.signals)
    genuinely_quiet = max_directional_signal_strength(verdict.signals) <= CONDOR_MAX_SIGNAL_STRENGTH
    iv_level = atm_iv(chain, spot, t_years)
    if (
        not verdict.hard_block
        and genuinely_quiet
        and range_score >= CONDOR_RANGE_SCORE_THRESHOLD
        and iv_level
        and iv_level >= CONDOR_MIN_IV
    ):
        try:
            card = _iron_condor_card(ratio, chain, spot, t_years, range_score)
        except ValueError:
            return None
        card.data_as_of = now.isoformat()
        return card

    return None
