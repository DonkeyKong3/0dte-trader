"""Regression tests for the IV-estimation bug found in production: on an
ordinary SPY session, Yahoo's raw `impliedVolatility` field was reporting
ATM IV around 2-5% annualized (realistic SPY IV is rarely below ~10%),
which silently suppressed every magnitude-dependent calculation -- the
prediction's expected move, delta-based strike selection, and IV skew.

The fix prefers solving IV from each leg's live bid/ask mid price (which
we always have fresh) over trusting the chain's raw field, falling back to
the raw field only when there's no usable price to solve from.
"""
import pytest

from app.data.market_data import OptionLeg, OptionsChain, bs_price, leg_effective_iv
from app.engine.signals import iv_skew_signal
from app.engine.spreads import atm_iv

RATE = 0.05


def _priced_leg(spot, strike, t_years, true_iv, is_call, bogus_raw_iv):
    price = bs_price(spot, strike, t_years, true_iv, RATE, is_call)
    return OptionLeg(strike=strike, bid=price, ask=price, last=price, volume=50, open_interest=50, implied_vol=bogus_raw_iv)


def test_leg_effective_iv_prefers_price_solved_over_bogus_raw_field():
    spot, strike, t_years, true_iv = 500.0, 500.0, 0.02, 0.20
    leg = _priced_leg(spot, strike, t_years, true_iv, is_call=True, bogus_raw_iv=0.03)
    result = leg_effective_iv(leg, spot, t_years, is_call=True, rate=RATE)
    assert result == pytest.approx(true_iv, abs=0.01)


def test_leg_effective_iv_falls_back_to_raw_field_with_no_usable_price():
    leg = OptionLeg(strike=500.0, bid=0.0, ask=0.0, last=0.0, volume=0, open_interest=0, implied_vol=0.18)
    result = leg_effective_iv(leg, 500.0, 0.02, is_call=True)
    assert result == pytest.approx(0.18)


def test_leg_effective_iv_none_when_nothing_usable():
    leg = OptionLeg(strike=500.0, bid=0.0, ask=0.0, last=0.0, volume=0, open_interest=0, implied_vol=None)
    assert leg_effective_iv(leg, 500.0, 0.02, is_call=True) is None


def test_atm_iv_uses_price_solved_iv_not_bogus_raw_field():
    spot, t_years, true_iv = 500.0, 0.02, 0.18
    call = _priced_leg(spot, 500.0, t_years, true_iv, is_call=True, bogus_raw_iv=0.02)
    put = _priced_leg(spot, 500.0, t_years, true_iv, is_call=False, bogus_raw_iv=0.02)
    chain = OptionsChain(expiration="2026-01-06", underlying_price=spot, calls=[call], puts=[put])
    result = atm_iv(chain, spot, t_years)
    assert result == pytest.approx(true_iv, abs=0.01)


def test_iv_skew_signal_uses_price_solved_iv_not_bogus_raw_field():
    """The clearest demonstration of the bug: with the bogus raw fields
    (call=0.40, put=0.02) the OLD code would call this bullish (call richer
    than put); the true prices imply the opposite (put richer -> bearish).
    A pre-fix run of this test fails with direction == "bullish"."""
    spot, t_years = 500.0, 0.02
    call = _priced_leg(spot, 500.0, t_years, true_iv=0.15, is_call=True, bogus_raw_iv=0.40)
    put = _priced_leg(spot, 500.0, t_years, true_iv=0.22, is_call=False, bogus_raw_iv=0.02)
    chain = OptionsChain(expiration="2026-01-06", underlying_price=spot, calls=[call], puts=[put])

    result = iv_skew_signal(chain, spot, t_years)

    assert result.direction == "bearish"


def test_iv_skew_signal_without_spot_or_t_years_falls_back_to_raw_field():
    """Backward-compatible call shape (chain only) still works, just without
    the price-solve robustness -- used only when a caller genuinely has no
    spot/t_years context."""
    chain = OptionsChain(
        expiration="2026-01-06",
        underlying_price=500.0,
        calls=[OptionLeg(strike=500.0, bid=1.0, ask=1.2, last=1.1, volume=10, open_interest=10, implied_vol=0.15)],
        puts=[OptionLeg(strike=500.0, bid=1.0, ask=1.2, last=1.1, volume=10, open_interest=10, implied_vol=0.15)],
    )
    result = iv_skew_signal(chain)
    assert result.direction == "neutral"  # equal raw IVs -> flat skew
