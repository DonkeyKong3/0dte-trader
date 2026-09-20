"""Same-day high-impact economic event awareness.

Two kinds of events:
  1. RULE-BASED: events that fall on a deterministic day of the calendar
     (weekly jobless claims = every Thursday, NFP = first Friday of the
     month, ISM Manufacturing = 1st business day, ISM Services = 3rd
     business day). These are computed programmatically so they never go
     stale.
  2. CURATED: events the government sets on a specific date each cycle with
     no simple rule (FOMC decisions, CPI, PPI, PCE, retail sales). These are
     hand-maintained in CURATED_EVENTS below.

MAINTENANCE: CURATED_EVENTS must be refreshed periodically against the
official sources:
  - FOMC meeting dates: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
  - BLS release schedule (CPI, PPI, NFP, Employment Situation):
    https://www.bls.gov/schedule/news_release/
  - BEA release schedule (PCE, GDP): https://www.bea.gov/news/schedule
This module intentionally ships with only entries verified against those
schedules; if a date has not been confirmed it is left out rather than
guessed, since a missed/incorrect economic-event flag is worse than none
displayed and the confidence engine should not be trusted with fabricated
event dates.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from config import TZ

# --- Curated, hand-verified high-impact events (date -> event) ---
# Format: "YYYY-MM-DD": [(name, time_et_HH:MM, impact)]
# impact: "high" (FOMC, CPI, NFP, PCE) or "medium" (PPI, retail sales, ISM)
CURATED_EVENTS: dict[str, list[tuple[str, str, str]]] = {
    # 2026 FOMC decision days (second day of each two-day meeting, per the
    # Federal Reserve's published 2026 calendar). Verify/update annually.
    "2026-01-28": [("FOMC Rate Decision", "14:00", "high")],
    "2026-03-18": [("FOMC Rate Decision", "14:00", "high")],
    "2026-04-29": [("FOMC Rate Decision", "14:00", "high")],
    "2026-06-17": [("FOMC Rate Decision", "14:00", "high")],
    "2026-07-29": [("FOMC Rate Decision", "14:00", "high")],
    "2026-09-16": [("FOMC Rate Decision", "14:00", "high")],
    "2026-10-28": [("FOMC Rate Decision", "14:00", "high")],
    "2026-12-09": [("FOMC Rate Decision", "14:00", "high")],
}

# Additional CPI/PPI/PCE/retail-sales dates should be appended here as they
# are confirmed from the BLS/BEA schedules above -- left sparse on purpose.


@dataclass
class EconEvent:
    name: str
    time_et: dt.time
    impact: str  # "high" | "medium"


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """weekday: Monday=0..Sunday=6. n=1 for first occurrence, etc."""
    d = dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d += dt.timedelta(days=offset)
    d += dt.timedelta(weeks=n - 1)
    return d


def _business_days_of_month(year: int, month: int, count: int) -> list[dt.date]:
    d = dt.date(year, month, 1)
    days = []
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def _rule_based_events(target: dt.date) -> list[EconEvent]:
    events: list[EconEvent] = []

    # Weekly initial jobless claims: every Thursday, 8:30am ET
    if target.weekday() == 3:
        events.append(EconEvent("Initial Jobless Claims", dt.time(8, 30), "medium"))

    # Non-Farm Payrolls: first Friday of the month, 8:30am ET
    first_friday = _nth_weekday(target.year, target.month, 4, 1)
    if target == first_friday:
        events.append(EconEvent("Non-Farm Payrolls", dt.time(8, 30), "high"))

    # ISM Manufacturing PMI: 1st business day of month, 10:00am ET
    biz_days = _business_days_of_month(target.year, target.month, 3)
    if biz_days and target == biz_days[0]:
        events.append(EconEvent("ISM Manufacturing PMI", dt.time(10, 0), "medium"))

    # ISM Services PMI: ~3rd business day of month, 10:00am ET
    if len(biz_days) >= 3 and target == biz_days[2]:
        events.append(EconEvent("ISM Services PMI", dt.time(10, 0), "medium"))

    return events


def _curated_events(target: dt.date) -> list[EconEvent]:
    key = target.isoformat()
    out = []
    for name, time_str, impact in CURATED_EVENTS.get(key, []):
        h, m = (int(x) for x in time_str.split(":"))
        out.append(EconEvent(name, dt.time(h, m), impact))
    return out


def events_for_date(target: dt.date) -> list[EconEvent]:
    return sorted(_rule_based_events(target) + _curated_events(target), key=lambda e: e.time_et)


def high_impact_events_today(now: dt.datetime | None = None) -> list[EconEvent]:
    now = now or dt.datetime.now(TZ)
    return [e for e in events_for_date(now.date()) if e.impact == "high"]


def in_blackout_window(now: dt.datetime, minutes_before: int, minutes_after: int) -> EconEvent | None:
    """Returns the event we're currently blacked out for, or None."""
    for event in events_for_date(now.date()):
        event_dt = dt.datetime.combine(now.date(), event.time_et, tzinfo=TZ)
        window_start = event_dt - dt.timedelta(minutes=minutes_before)
        window_end = event_dt + dt.timedelta(minutes=minutes_after)
        if window_start <= now <= window_end:
            return event
    return None
