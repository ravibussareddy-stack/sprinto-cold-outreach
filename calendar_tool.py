"""check_calendar - is a set of meeting slots free?

Tool description (what / when / when not / returns):
- What: returns up to N free business-hour slots from now onwards.
- When: a prospect has confirmed interest and we need slots to propose.
- When not: to list meetings, or for dates beyond SLOT_LOOKAHEAD_DAYS.
- Returns: {"status": "free" | "no-calendar-access", "slots": [ISO datetimes]}.

Source: CALENDAR_BUSY_FILE (busy intervals as JSON). Live Google Calendar
free/busy needs OAuth and is not wired up yet, so without the file the tool
reports no-calendar-access and the agent escalates to the Growth Lead.
"""
import datetime as dt
import json
from zoneinfo import ZoneInfo

import config


def _busy_intervals() -> list[tuple[dt.datetime, dt.datetime]]:
    with open(config.CALENDAR_BUSY_FILE) as f:
        raw = json.load(f)
    return [(dt.datetime.fromisoformat(a), dt.datetime.fromisoformat(b)) for a, b in raw]


def check_calendar(now: dt.datetime, exclude: set[str] | None = None) -> dict:
    if not config.CALENDAR_BUSY_FILE:
        return {"status": "no-calendar-access", "slots": []}
    busy = _busy_intervals()          # OSError here is transient and gets retried
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
                return {"status": "free", "slots": slots}
    return {"status": "free", "slots": slots}
