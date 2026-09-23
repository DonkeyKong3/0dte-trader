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
6. **Track record.** Every confident card is also paper-tracked: at most
   one open "trade" at a time, re-priced against the live options chain
   every poll cycle, and automatically resolved (won/lost) the moment it
   hits its own profit target, stop loss, or force-close cutoff -- the
   exact same rules the card told you to manage it by. Losses get a
   best-effort, auto-generated reason tag (wrong direction call, right
   direction but stopped out anyway, a high-impact econ event that day,
   or an entry on only borderline confidence) so patterns are visible for
   review, rather than a black box. This is diagnostic data for you (or a
   future session) to act on by hand-tuning `config.py` -- nothing here
   automatically rewrites its own weights or thresholds.
7. **Predicted movement -- always on.** Separate from the trade suggestion
   (which is gated and can legitimately say nothing), every cycle also
   buckets into 5 always-shown buckets -- Big Down / Small Down / Flat /
   Small Up / Big Up -- so there's always *some* read on where SPY looks
   likely to go even when nothing is confident enough to trade. Direction
   and magnitude are deliberately independent evidence, not the same
   number split in two:
   - **Direction & confidence** come from the same weighted 5-signal
     consensus as the trade-confidence gate.
   - **Magnitude** (small vs big) comes from the options market's own
     IV-implied expected move for the horizon (spot &times; ATM IV &times;
     &radic;time -- the same "expected move" math options traders use to
     size ranges), falling back to realized ATR when no chain is
     available. A strong directional score with a low implied move still
     shows "small"; a big implied move can pair with only moderate
     directional confidence. Both the expected-move % and its source
     (`iv` or `atr`) are shown on the card. **IV here is solved from each
     leg's live bid/ask mid price, not read from the chain's raw
     `impliedVolatility` field** -- confirmed in production that field can
     report SPY ATM IV in the low single digits (realistic SPY IV is
     rarely below ~10%) for thin, near-expiry 0DTE contracts, which
     silently suppressed magnitude estimates and, more importantly, could
     flip the sign of the IV-skew signal used for direction. This affects
     delta-based strike selection too (`app/data/market_data.py:leg_effective_iv`).

   Every prediction is checked ~30 minutes later against what SPY actually
   did (`PREDICTION_HORIZON_MINUTES` in `config.py`) and scored both on an
   exact-bucket match and a softer "right direction" basis, surfaced in the
   dashboard's "Prediction accuracy" section. The "Preview last session"
   demo shows this with real hindsight immediately, since that session's
   future bars already happened.

## Running it

**Quick start (recommended):** double-click/run the script for your OS. It
creates the virtual environment on first run, installs/updates dependencies,
and starts the server bound to your network so your phone can reach it too.

- Windows: right-click `run.ps1` -> **Run with PowerShell** (or run
  `.\run.ps1` from a PowerShell prompt in the project folder)
- macOS/Linux: `./run.sh` (first time: `chmod +x run.sh`)

It'll print two URLs, e.g.:

```
This PC:   http://localhost:8000
Phone/LAN: http://192.168.1.42:8000
```

**Manual setup**, if you'd rather do it yourself:

```bash
python3 -m venv .venv        # Windows: py -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`. The scheduler polls every 60 seconds while
the market is open (9:30am-4:00pm ET, Mon-Fri) and writes each cycle to a
local SQLite file (`signals.db`) so the dashboard can show a running
history/track record. Use the "Refresh now" button to force an immediate
cycle instead of waiting for the next poll.

**Note on SPY 0DTE availability:** SPY has same-day expirations on
Mon/Wed/Fri; Tue/Thu are gap days with no 0DTE chain, so the engine will
correctly report no options-based signal on those days.

## Using it from your iPhone

This is a Python web app -- iOS can't actually run Python/FastAPI/pandas
natively (no real wheel support in any iOS shell app, and options like
`iSH`/`a-Shell` emulate a slow, constrained Linux and would hit the same
kind of build failures you just saw on Windows, worse). The practical setup
instead: run it on a computer that's on, and view the dashboard from your
iPhone's browser as a client.

- **Same Wi-Fi as the computer:** run `run.ps1`/`run.sh`, then open the
  printed `Phone/LAN` URL (e.g. `http://192.168.1.42:8000`) in Safari on
  your iPhone. Nothing else to install. The one-time catch on Windows: the
  first time you run it, Windows Firewall will likely prompt to allow Python
  through on "Private networks" -- allow it, or your phone won't be able to
  connect.
- **Away from home / different network:** the phone can't reach a
  `192.168.x.x` address over the internet. Two common ways around that,
  neither of which requires opening ports on your router:
  - [Tailscale](https://tailscale.com) (free for personal use) -- install it
    on the computer and on your iPhone, then browse to the computer's
    Tailscale address from anywhere. Simplest and keeps everything private.
  - Deploy the app itself to a small always-on host (a cheap VPS, Render,
    Fly.io, etc.) and browse to its public URL instead. More setup, but no
    "leave your PC on" requirement.

Either way, the app itself only needs to run in **one place** (your
computer, or a small server) -- your iPhone is just viewing the dashboard
in Safari, not running the engine.

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
- **Track record is paper-tracked, not a real fill.** It re-prices the
  same strikes against the live SPY chain, which is the same estimate
  methodology as the entry card -- it will not exactly match what a real
  broker fill would have done (slippage, bid/ask timing), but it's
  consistent with itself, so win-rate trends over time are meaningful
  even if any single trade's exact dollar P&L isn't execution-grade.
- **Only one trade tracked at a time.** If the engine stays confident
  across several poll cycles, that's treated as the same suggestion, not
  a new one each time -- otherwise win-rate stats would double-count a
  single real-world trade. A fresh card still displays live either way.
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
