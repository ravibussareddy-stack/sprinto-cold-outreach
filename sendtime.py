"""When to send follow-ups: a send window that starts from published research and learns from your replies.

Research prior (rubric send_window, default Tue-Thu, 08:00-11:00 recipient-local time): most cold-email
studies put the best reply rates mid-week in the morning, with a modest gap between best and worst
windows (about 1.5-2.5 points of reply rate). Sources: Belkins 2026 study (Wed/Thu, 8am-noon),
Instantly 2026 benchmark (Tue/Wed peak), Smartlead (94% of replies within 72 hours).

mode "auto": once at least min_sends_to_learn emails have been sent in tracked conversations, the
window switches to what your own replies show - the best days and the best 3-hour block. Until then,
and in mode "fixed", the rubric's window is used. Replies from prospects are always answered at once;
the window applies to unsolicited follow-ups only.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import config
import db
import rubric

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
BLOCK_HOURS = 3


def observations() -> list[dict]:
    """Every tracked outbound email (cold or follow-up) and whether a reply came before our next email."""
    if not db.enabled():
        return []
    try:
        db._init()
        with db._conn() as c:
            rows = c.execute("""
                SELECT e.created_at,
                       EXISTS (SELECT 1 FROM emails r WHERE r.thread_id = e.thread_id AND r.kind = 'reply'
                               AND r.created_at > e.created_at
                               AND r.created_at < coalesce((SELECT min(n.created_at) FROM emails n
                                    WHERE n.thread_id = e.thread_id AND n.created_at > e.created_at
                                    AND (n.kind = 'cold' OR n.kind LIKE 'followup%%')), 'infinity'::timestamptz)) AS replied
                FROM emails e
                WHERE e.thread_id IS NOT NULL AND (e.kind = 'cold' OR e.kind LIKE 'followup%%')""").fetchall()
    except Exception:
        return []
    return [{"at": r[0], "replied": bool(r[1])} for r in rows]


def stats(rows: list[dict], tz: ZoneInfo) -> dict:
    by_day = {d: [0, 0] for d in DAY_NAMES}
    by_hour = {h: [0, 0] for h in range(24)}
    for r in rows:
        local = r["at"].astimezone(tz)
        d, h = DAY_NAMES[local.weekday()], local.hour
        by_day[d][0] += 1
        by_hour[h][0] += 1
        if r["replied"]:
            by_day[d][1] += 1
            by_hour[h][1] += 1
    return {"n": len(rows), "replied": sum(1 for r in rows if r["replied"]), "by_day": by_day, "by_hour": by_hour}


def _rate(sent_replied):
    s, r = sent_replied
    return r / s if s else 0.0


def best_window(rows: list[dict] | None = None) -> dict:
    sw = rubric.load()["send_window"]
    tz = ZoneInfo(config.SLOT_TIMEZONE)
    prior = {"days": sw["days"], "start_hour": sw["start_hour"], "end_hour": sw["end_hour"], "timezone": config.SLOT_TIMEZONE}
    if sw["mode"] == "fixed":
        return prior | {"source": "fixed", "n": None}
    rows = observations() if rows is None else rows
    st = stats(rows, tz)
    if st["n"] < sw["min_sends_to_learn"] or st["replied"] == 0:
        return prior | {"source": "research", "n": st["n"]}
    days = sorted((d for d in DAY_NAMES if st["by_day"][d][0] >= 3), key=lambda d: _rate(st["by_day"][d]), reverse=True)[:3]
    blocks = []
    for start in range(6, 22 - BLOCK_HOURS + 1):
        sent = sum(st["by_hour"][h][0] for h in range(start, start + BLOCK_HOURS))
        rep = sum(st["by_hour"][h][1] for h in range(start, start + BLOCK_HOURS))
        if sent >= 3:
            blocks.append((rep / sent, start))
    if not days or not blocks:
        return prior | {"source": "research", "n": st["n"]}
    start = max(blocks)[1]
    return {"days": days, "start_hour": start, "end_hour": start + BLOCK_HOURS, "timezone": config.SLOT_TIMEZONE,
            "source": "observed", "n": st["n"]}


def in_window(now: dt.datetime, window: dict) -> bool:
    local = now.astimezone(ZoneInfo(window["timezone"]))
    return DAY_NAMES[local.weekday()] in window["days"] and window["start_hour"] <= local.hour < window["end_hour"]


def describe(window: dict) -> str:
    days = ", ".join(d.title() for d in window["days"])
    src = {"observed": f"learnt from {window['n']} sends", "research": "research default", "fixed": "fixed in the rubric"}[window["source"]]
    return f"{days} {window['start_hour']:02d}:00-{window['end_hour']:02d}:00 ({src})"
