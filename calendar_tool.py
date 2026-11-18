"""check_calendar - is a set of meeting slots free?

Tool description (what / when / when not / returns):
- What: returns up to N free business-hour slots from now onwards.
- When: a prospect has confirmed interest and we need slots to propose.
- When not: to list meetings, or for dates beyond SLOT_LOOKAHEAD_DAYS.
- Returns: {"status": "free" | "no-calendar-access", "slots": [ISO datetimes]}.

Sources, in order:
1. Calendly (CALENDLY_TOKEN): live busy times from the calendars connected to
   your Calendly account (read-only, availability:read).
2. CALENDAR_BUSY_FILE: busy intervals as JSON, for tests or offline runs.
Neither set -> no-calendar-access, and the agent escalates to the Growth Lead.
"""
import datetime as dt
import json
from zoneinfo import ZoneInfo

import requests

import config

CALENDLY_API = "https://api.calendly.com"


def _calendly_busy(now: dt.datetime, end: dt.datetime | None = None) -> list[tuple[dt.datetime, dt.datetime]]:
    """Busy intervals from `now` to `end` (default: SLOT_LOOKAHEAD_DAYS; Calendly allows at most 7 days per call)."""
    h = {"Authorization": f"Bearer {config.CALENDLY_TOKEN}"}
    me = requests.get(f"{CALENDLY_API}/users/me", headers=h, timeout=config.REQUEST_TIMEOUT)
    me.raise_for_status()
    user = me.json()["resource"]["uri"]
    start = now.astimezone(dt.timezone.utc) + dt.timedelta(minutes=1)
    if end is None:
        end = start + dt.timedelta(days=min(config.SLOT_LOOKAHEAD_DAYS, 7)) - dt.timedelta(minutes=2)
    end = end.astimezone(dt.timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    r = requests.get(f"{CALENDLY_API}/user_busy_times", headers=h, timeout=config.REQUEST_TIMEOUT,
                     params={"user": user, "start_time": start.strftime(fmt), "end_time": end.strftime(fmt)})
    r.raise_for_status()
    iso = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return [(iso(b["start_time"]), iso(b["end_time"])) for b in r.json().get("collection", [])]


def _busy_intervals(now: dt.datetime) -> tuple[str, list[tuple[dt.datetime, dt.datetime]]]:
    if config.CALENDLY_TOKEN:
        return "calendly", _calendly_busy(now)
    with open(config.CALENDAR_BUSY_FILE) as f:
        raw = json.load(f)
    return "file", [(dt.datetime.fromisoformat(a), dt.datetime.fromisoformat(b)) for a, b in raw]


MAX_PROPOSED_DAYS_AHEAD = 14


def slot_free(start: dt.datetime, now: dt.datetime, minutes: int = 20) -> dict:
    """Is one specific time, named by the prospect, free?
    Returns {"status": "free" | "busy" | "out-of-range" | "no-calendar-access"}."""
    if not config.CALENDLY_TOKEN and not config.CALENDAR_BUSY_FILE:
        return {"status": "no-calendar-access"}
    end = start + dt.timedelta(minutes=minutes)
    if start <= now + dt.timedelta(hours=1) or start > now + dt.timedelta(days=MAX_PROPOSED_DAYS_AHEAD):
        return {"status": "out-of-range"}
    if config.CALENDLY_TOKEN:
        busy = _calendly_busy(start - dt.timedelta(minutes=2), end + dt.timedelta(minutes=1))
    else:
        _, busy = _busy_intervals(now)
    clash = any(s < end and start < e for s, e in busy)
    return {"status": "busy" if clash else "free"}


def check_calendar(now: dt.datetime, exclude: set[str] | None = None) -> dict:
    if not config.CALENDLY_TOKEN and not config.CALENDAR_BUSY_FILE:
        return {"status": "no-calendar-access", "slots": []}
    source, busy = _busy_intervals(now)   # network / file errors are transient and get retried
    tz = ZoneInfo(config.SLOT_TIMEZONE)
    now_local = now.astimezone(tz)
    exclude = exclude or set()
    slots = []
    for day in range(1, config.SLOT_LOOKAHEAD_DAYS + 1):
        d = (now_local + dt.timedelta(days=day)).date()
        if d.weekday() >= 5:          # skip Sat/Sun
            continue
        for hour in config.SLOT_HOURS:
            start = dt.datetime(d.year, d.month, d.day, hour, tzinfo=tz)
            end = start + dt.timedelta(hours=1)
            iso = start.isoformat()
            if iso in exclude:
                continue
            if any(s < end and start < e for s, e in busy):
                continue
            slots.append(iso)
            if len(slots) == config.SLOTS_TO_OFFER:
                return {"status": "free", "slots": slots, "source": source, "busy_count": len(busy)}
    return {"status": "free", "slots": slots, "source": source, "busy_count": len(busy)}
