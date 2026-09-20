import pytest

from app.data.market_data import OptionLeg, OptionsChain
from app.engine.spreads import reprice_trade


def _leg(strike, bid, ask):
    return OptionLeg(strike=strike, bid=bid, ask=ask, last=(bid + ask) / 2, volume=100, open_interest=100, implied_vol=0.15)


def test_reprice_credit_spread():
    chain = OptionsChain(
        expiration="2026-01-06",
        underlying_price=500.0,
        calls=[],
        puts=[_leg(499.0, 0.28, 0.32), _leg(498.0, 0.18, 0.22)],
    )
    trade = {"strategy": "bull_put_credit", "short_strike": 4990, "long_strike": 4980}
    price = reprice_trade(trade, chain, ratio=10.0)
    assert price == pytest.approx(1.0)  # (0.30 - 0.20) * 10


def test_reprice_debit_spread():
    chain = OptionsChain(
        expiration="2026-01-06",
        underlying_price=500.0,
        calls=[_leg(500.0, 1.0, 1.2), _leg(505.0, 0.3, 0.5)],
        puts=[],
    )
    trade = {"strategy": "bull_call_debit", "short_strike": 5050, "long_strike": 5000}
    price = reprice_trade(trade, chain, ratio=10.0)
    assert price == pytest.approx(7.0)  # (1.1 - 0.4) * 10


def test_reprice_iron_condor():
    chain = OptionsChain(
        expiration="2026-01-06",
        underlying_price=500.0,
        calls=[_leg(505.0, 0.25, 0.3), _leg(510.0, 0.1, 0.14)],
        puts=[_leg(495.0, 0.22, 0.26), _leg(490.0, 0.1, 0.12)],
    )
    trade = {
        "strategy": "iron_condor",
        "call_short_strike": 5050,
        "call_long_strike": 5100,
        "put_short_strike": 4950,
        "put_long_strike": 4900,
    }
    price = reprice_trade(trade, chain, ratio=10.0)
    assert price == pytest.approx(2.85)  # (0.275-0.12) + (0.24-0.11), * 10


def test_reprice_returns_none_without_chain_or_ratio():
    trade = {"strategy": "bull_put_credit", "short_strike": 5000, "long_strike": 4990}
    assert reprice_trade(trade, None, 10.0) is None
    chain = OptionsChain(expiration="2026-01-06", underlying_price=500.0, calls=[], puts=[_leg(500.0, 1.0, 1.0)])
    assert reprice_trade(trade, chain, None) is None
