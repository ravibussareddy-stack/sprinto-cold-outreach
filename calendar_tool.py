"""check_calendar - is a set of meeting slots free?

Tool description (what / when / when not / returns):
- What: returns up to N free business-hour slots from now onwards.
- When: a prospect has confirmed interest and we need slots to propose.
- When not: to list meetings, or for dates beyond SLOT_LOOKAHEAD_DAYS.
- Returns: {"status": "free" | "no-calendar-access", "slots": [ISO datetimes]}.

Sources, in order:
1. Calendly booking (CALENDLY_BOOKING=1): times your Calendly event type can actually
   be booked at, and book_calendly() books the agreed time - it then appears on the
   Google Calendar connected to Calendly, with a Meet link, and Calendly sends the invite.
2. Calendly busy times (CALENDLY_TOKEN): live busy times from your connected calendars.
3. CALENDAR_BUSY_FILE: busy intervals as JSON, for tests or offline runs.
None set -> no-calendar-access, and the agent escalates to the Growth Lead.
"""
import datetime as dt
import json
from zoneinfo import ZoneInfo

import requests

import config
from retry import PermanentError

CALENDLY_API = "https://api.calendly.com"
_FMT = "%Y-%m-%dT%H:%M:%SZ"
_event_type_cache: dict | None = None


def _h() -> dict:
    return {"Authorization": f"Bearer {config.CALENDLY_TOKEN}", "Content-Type": "application/json"}


def _utc(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).strftime(_FMT)


def _parse(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def event_type() -> dict:
    """The Calendly event type meetings are booked into: CALENDLY_EVENT_TYPE (its URI),
    else your first active event type. {"uri", "name", "duration", "location_kind"}."""
    global _event_type_cache
    if _event_type_cache:
        return _event_type_cache
    if config.CALENDLY_EVENT_TYPE:
        r = requests.get(config.CALENDLY_EVENT_TYPE, headers=_h(), timeout=config.REQUEST_TIMEOUT)
        r.raise_for_status()
        t = r.json()["resource"]
    else:
        me = requests.get(f"{CALENDLY_API}/users/me", headers=_h(), timeout=config.REQUEST_TIMEOUT)
        me.raise_for_status()
        r = requests.get(f"{CALENDLY_API}/event_types", headers=_h(), timeout=config.REQUEST_TIMEOUT,
                         params={"user": me.json()["resource"]["uri"], "active": "true"})
        r.raise_for_status()
        types = r.json().get("collection", [])
        if not types:
            raise PermanentError("no active Calendly event type to book into")
        t = types[0]
    locs = t.get("locations") or []
    _event_type_cache = {"uri": t["uri"], "name": t.get("name"), "duration": t.get("duration") or 30,
                         "location_kind": locs[0].get("kind") if locs else None}
    return _event_type_cache


def _available_times(start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """Start times the event type can be booked at (Calendly allows at most 7 days per call)."""
    r = requests.get(f"{CALENDLY_API}/event_type_available_times", headers=_h(), timeout=config.REQUEST_TIMEOUT,
                     params={"event_type": event_type()["uri"], "start_time": _utc(start), "end_time": _utc(end)})
    r.raise_for_status()
    return [_parse(t["start_time"]) for t in r.json().get("collection", []) if t.get("status") == "available"]


def book_calendly(start_iso: str, invitee_name: str, invitee_email: str) -> dict:
    """Book the agreed time into the Calendly event type. Calendly puts it on the connected
    Google Calendar and emails the invitee. A 4xx (e.g. time no longer available) is permanent."""
    et = event_type()
    body = {"event_type": et["uri"], "start_time": _utc(dt.datetime.fromisoformat(start_iso)),
            "invitee": {"name": invitee_name, "email": invitee_email, "timezone": config.SLOT_TIMEZONE}}
    if et["location_kind"]:
        body["location"] = {"kind": et["location_kind"]}
    r = requests.post(f"{CALENDLY_API}/invitees", headers=_h(), json=body, timeout=config.REQUEST_TIMEOUT)
    if 400 <= r.status_code < 500:
        raise PermanentError(f"Calendly refused the booking ({r.status_code}): {r.text[:300]}")
    r.raise_for_status()
    res = r.json().get("resource", {})
    return {"event": res.get("event"), "invitee": res.get("uri"),
            "cancel_url": res.get("cancel_url"), "reschedule_url": res.get("reschedule_url")}


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
    if config.CALENDLY_BOOKING and config.CALENDLY_TOKEN:
        # Bookable means: free AND inside the event type's availability rules.
        times = _available_times(start - dt.timedelta(minutes=1), start + dt.timedelta(minutes=1))
        return {"status": "free" if any(abs((t - start).total_seconds()) < 60 for t in times) else "busy"}
    if config.CALENDLY_TOKEN:
        busy = _calendly_busy(start - dt.timedelta(minutes=2), end + dt.timedelta(minutes=1))
    else:
        _, busy = _busy_intervals(now)
    clash = any(s < end and start < e for s, e in busy)
    return {"status": "busy" if clash else "free"}


def _bookable_slots(now: dt.datetime, exclude: set[str]) -> dict:
    """Pick SLOTS_TO_OFFER bookable times, one per day, preferring SLOT_HOURS (e.g. 10:00, 15:00)."""
    tz = ZoneInfo(config.SLOT_TIMEZONE)
    start = now + dt.timedelta(hours=2)
    times = _available_times(start, start + dt.timedelta(days=min(config.SLOT_LOOKAHEAD_DAYS, 7)) - dt.timedelta(minutes=5))
    local = [t.astimezone(tz) for t in times if t.astimezone(tz).isoformat() not in exclude]
    by_day: dict = {}
    for t in local:
        by_day.setdefault(t.date(), []).append(t)
    slots = []
    for day in sorted(by_day):
        options = by_day[day]
        preferred = [t for t in options if t.hour in config.SLOT_HOURS and t.minute == 0]
        slots.append((preferred or options)[0].isoformat())
        if len(slots) == config.SLOTS_TO_OFFER:
            break
    return {"status": "free", "slots": slots, "source": "calendly-bookable", "busy_count": None}


def check_calendar(now: dt.datetime, exclude: set[str] | None = None) -> dict:
    if config.CALENDLY_BOOKING and config.CALENDLY_TOKEN:
        return _bookable_slots(now, exclude or set())
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
