"""Central configuration: market hours, thresholds, spread parameters.

All times are US/Eastern since that's the exchange's clock.
"""
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")

# --- Underlying / proxy ---
PROXY_TICKER = "SPY"          # free-data-friendly proxy for SPX
TARGET_TICKER = "^GSPC"       # SPX index, used only to compute live SPX/SPY ratio

# --- Market hours (ET) ---
MARKET_OPEN = (9, 30)
MARKET_CLOSE = (16, 0)
OPENING_RANGE_MINUTES = 15    # opening-range breakout window
NO_NEW_TRADES_AFTER = (15, 15)   # stop suggesting NEW entries this late (0DTE gamma risk)
FORCE_CLOSE_BY = (15, 45)        # hard close-out recommendation time, before the last liquidity crunch

# --- Polling ---
POLL_INTERVAL_SECONDS = 60

# --- Confidence gating ---
# Weighted signal score must clear this (0-100) before a trade is suggested at all.
CONFIDENCE_THRESHOLD = 70
# Individual signal weights (must sum to 100)
SIGNAL_WEIGHTS = {
    "trend": 25,
    "momentum": 20,
    "volume": 20,
    "opening_range": 15,
    "iv_skew": 20,
}

# --- Economic event handling ---
# Minutes before/after a high-impact release where we suppress new entries entirely.
ECON_BLACKOUT_MINUTES_BEFORE = 30
ECON_BLACKOUT_MINUTES_AFTER = 15
# Confidence penalty (subtracted, 0-100 scale) applied on days that merely
# HAVE a high-impact event later, even outside the blackout window.
ECON_EVENT_DAY_PENALTY = 10

# --- Spread construction ---
DEFAULT_SPREAD_WIDTH_SPX = 10     # points wide, SPX credit/debit spreads
CONDOR_SPREAD_WIDTH_SPX = 10
STOP_LOSS_MULTIPLE = 1.5          # close if loss reaches 1.5x credit received (credit spreads)
PROFIT_TARGET_FRACTION = 0.6      # close credit spreads at 60% of max profit captured
DEBIT_STOP_LOSS_FRACTION = 0.5    # close debit spreads if premium drops 50%
DEBIT_PROFIT_TARGET_FRACTION = 0.75  # close debit spreads at +75% premium gain

# --- Iron condor (range-bound) eligibility ---
CONDOR_RANGE_SCORE_THRESHOLD = 70   # how "quiet" the market must look (0-100)
CONDOR_MIN_IV = 0.10                # minimum ATM annualized IV to bother selling premium
TARGET_SHORT_DELTA = 0.18           # standard 0DTE credit-spread short-leg delta

# --- Database ---
DB_PATH = "signals.db"
