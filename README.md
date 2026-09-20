# SPX 0DTE Spread Signals

A single-purpose dashboard: it watches the market during the trading day and
tells you when (and only when) it's confident enough to suggest opening a
same-day-expiration (0DTE) SPX put/call spread or iron condor -- and exactly
how to manage and close it. If the evidence isn't there, it says nothing.

**This is informational/educational software, not financial advice, and it
does not place or manage trades.** See [Disclaimer](#disclaimer).

## How it works

1. **Data (free sources only).** True real-time SPX index/options data is a
   paid feed everywhere. Instead, the engine pulls free, near-real-time
   1-minute bars and the same-day options chain for **SPY** (the ETF that
   tracks SPX almost perfectly) via Yahoo Finance (`yfinance`), then scales
   strikes/premiums to SPX terms using the **live** SPX/SPY ratio (it
   drifts, so it's computed fresh every cycle rather than assumed to be
   ~10x).
2. **Signals.** Five independent technical/market-structure signals are
   computed every cycle, each producing a direction (bullish/bearish/
   neutral) and a 0-100 strength:
   - **Trend** -- price vs VWAP and EMA9/EMA20 alignment
   - **Momentum** -- 14-period RSI
   - **Volume** -- volume z-score confirming (or not) the current price move
   - **Opening range** -- breakout/breakdown of the first 15 minutes' range
   - **IV skew** -- put vs call implied vol near the money (hedging demand)
3. **Confidence gating.** Signals are combined into a single weighted score
   (weights in `config.py`). A trade is only ever suggested when:
   - the score clears `CONFIDENCE_THRESHOLD` (default 70/100), **and**
   - we're not inside a blackout window around a high-impact economic
     event (FOMC, CPI, NFP, PCE, ISM, weekly jobless claims -- see
     `app/data/economic_calendar.py`), **and**
   - it's before the daily cutoff for new 0DTE entries (default 3:15pm ET,
     since gamma risk accelerates fast into the close).

   Otherwise the dashboard shows **"No confident trade -- standing aside."**
   On a day when a high-impact release is merely scheduled later (outside
   the blackout window), the required bar is raised via a confidence
   penalty rather than an outright block.
4. **Strategy selection.** When directionally confident:
   - Strong momentum + volume confirmation -> **debit spread** (buying
     cheap convexity into a move already underway).
   - Otherwise -> **credit spread** (selling premium at a ~0.18-delta
     short strike, the standard 0DTE income structure).
   When *not* directionally confident but trend/momentum/volume/opening-range
   all read quiet **and** implied vol is rich enough to be worth selling,
   it separately evaluates an **iron condor** -- its own confidence check,
   not a fallback for "couldn't decide."
5. **Exit rules, given up front.** Every trade card ships with a profit
   target, a stop loss, and a hard time-based force-close -- because the
   app has no brokerage connection and can't know if/when you actually
   opened the position, these are the rules to manage it by rather than a
   live "close now" push.

## Running it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://localhost:8000`. The scheduler polls every 60 seconds while
the market is open (9:30am-4:00pm ET, Mon-Fri) and writes each cycle to a
local SQLite file (`signals.db`) so the dashboard can show a running
history/track record. Use the "Refresh now" button to force an immediate
cycle instead of waiting for the next poll.

**Note on SPY 0DTE availability:** SPY has same-day expirations on
Mon/Wed/Fri; Tue/Thu are gap days with no 0DTE chain, so the engine will
correctly report no options-based signal on those days.

## Configuration

All thresholds, weights, market-hours, and spread parameters live in
`config.py` -- confidence threshold, signal weights, spread width, stop-loss/
profit-target fractions, the new-entry cutoff time, and the economic-event
blackout window.

`app/data/economic_calendar.py` needs periodic maintenance: FOMC dates are
hand-curated from the Fed's published calendar, and CPI/PPI/PCE dates should
be added the same way from the BLS/BEA release schedules (both linked in the
module docstring). NFP, ISM, and weekly jobless claims are computed by rule
and never go stale.

## Limitations (read before trusting this with real money)

- **Data is not execution-grade.** Yahoo Finance has no real-time SLA.
  The dashboard shows a data-as-of timestamp for exactly this reason --
  treat any recommendation as directionally useful, not tick-precise.
- **Strikes/premiums are estimates**, scaled from the SPY chain to SPX
  dollar terms. Always confirm against a live SPX quote before entering.
- **Rule-based, not machine-learned.** The signal weights and thresholds
  are a reasonable starting heuristic, not a backtested, statistically
  validated edge. Nothing here guarantees win rate or limits loss beyond
  the stop-loss rule it hands you -- you still have to honor it.
- **No backtesting yet.** A natural next step is replaying historical
  sessions through the same engine to see how the confidence gate would
  have performed before trusting it live.
- **No brokerage/execution integration.** It tells you what to do; you
  place and close the trade yourself.

## Possible future integrations

- A live financial-news feed (e.g. a real-time newswire connector) in
  place of/alongside the curated economic calendar, for intraday
  headline risk beyond scheduled releases.
- A Robinhood (or other brokerage) integration, if/when one exposes a
  documented API suitable for read-only account/position context or
  order placement -- evaluate scope and risk carefully before wiring up
  anything that can place real orders.
- Historical backtesting mode to validate the confidence gate before
  relying on it live.

## Disclaimer

This tool is for informational and educational purposes only. It is not
registered investment, legal, or tax advice, and nothing it outputs is a
recommendation to buy or sell any security or options contract. Options
trading, and 0DTE options in particular, carries substantial risk of rapid
and total loss of the capital at risk. Past or simulated performance of
this or any strategy does not guarantee future results. You are solely
responsible for your own trading decisions.
