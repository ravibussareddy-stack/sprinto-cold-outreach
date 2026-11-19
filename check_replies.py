"""Continue every conversation that is waiting for a reply (REPLY_MODE=gmail).

    python check_replies.py

For each thread with status 'awaiting' in Neon:
- look in Gmail for new replies to that cold email (inbox.py)
- feed each one to the saved LangGraph conversation, oldest first: an interested
  reply gets proposed times, a picked time gets a calendar invite, and so on
- no reply -> follow-ups on the rubric's timers (default: 3 days after our email, 3 days after
  follow-up 1, 2 days after follow-up 2), each with a new angle, sent only inside the send window
  (sendtime.py); then non_respondent. Silence after they showed interest gets scheduling nudges
  on the same timers, then a hand-off. Every reply starts the count again.
- the monthly budget (rubric) pauses everything when it is reached
- a declined reply is thanked and the contact becomes a cold lead
- when a conversation ends, learn.py turns it into lessons for the feedback repo
Runs on a schedule in GitHub Actions; safe to run as often as you like, because
replies already handled are remembered by Message-ID.
"""
import datetime as dt

import psycopg

import budget
import config
import db
import inbox
import learn
import llm
import meeting
import rubric
import sendtime
import tune


def log(msg: str):
    print(msg, flush=True)


# Where each contact stands once a conversation moves (contacts table, dashboard).
STATUS_BY_OUTCOME = {"booked": "meeting_booked", "not_interested": "cold_lead",
                     "non_respondent": "non_respondent", "no_reply": "non_respondent"}


def contact_status(state, waiting: bool) -> str:
    if not waiting:
        return STATUS_BY_OUTCOME.get(state.outcome, "escalated")
    return "engaged" if state.sentiment == "positive" else "contacted"


def wait_days(sent_in_this_silence: int) -> float:
    """Days of silence before the next step: follow-up 1, 2, 3, then giving up."""
    f = rubric.load()["followups"]
    steps = [f["after_first_days"], f["after_second_days"], f["after_third_days"]]
    return steps[sent_in_this_silence] if sent_in_this_silence < len(steps) else f["give_up_after_days"]


def check_thread(t: dict, now: dt.datetime, window: dict) -> str:
    c = t["ctx"]
    llm.context["domain"] = t["domain"]
    ctx = meeting.Ctx(**c, do_send=True, log=log, subject=t["subject"], thread_id=t["thread_id"], followups=True)
    processed = list(t["processed_ids"] or [])
    found = inbox.find_replies(t["subject"], t["created_at"], set(processed))
    state, waiting, touched = None, True, False
    for r in found:
        log(f"  reply from {r['from']} at {r['date']:%d %b %H:%M}: {r['text'][:120]!r}")
        db.log_email(t["thread_id"], t["domain"], "reply", f"Re: {t['subject']}", r["text"])
        ctx.in_reply_to = r["id"]
        state, waiting = meeting.resume(ctx, t["thread_id"], r["text"])
        processed.append(r["id"])
        touched = True
        if not waiting:
            break
    n = t.get("followups_sent") or 0
    days = wait_days(n)
    if not found and now - t["last_outbound_at"] >= dt.timedelta(days=days):
        sends_email = n < config.MAX_FOLLOWUPS
        if sends_email and not sendtime.in_window(now, window):
            return f"follow-up {n + 1} due ({days:g} days of silence), waiting for the send window: {sendtime.describe(window)}"
        log(f"  {days:g} days without a reply -> " + (f"follow-up {n + 1}" if sends_email else "closing"))
        ctx.in_reply_to = None
        state, waiting = meeting.resume(ctx, t["thread_id"], None)   # next follow-up, or close
        touched = True
    elif not found:
        due = t["last_outbound_at"] + dt.timedelta(days=days)
        if n < config.MAX_FOLLOWUPS:
            go = sendtime.next_open(due, window)
            return (f"no new reply; follow-up {n + 1} due {due:%a %d %b} after {days:g} days of silence, "
                    f"sent {go:%a %d %b %H:%M} {window['timezone']} (first slot in the send window)")
        return f"no new reply; conversation closes {due:%a %d %b %H:%M} UTC"
    if state is None:
        return "no new reply"
    db.update_thread(t["thread_id"], "awaiting" if waiting else "done", state.outcome or "awaiting_reply",
                     processed, touched_outbound=touched, replied=state.replied,
                     sentiment=state.sentiment, followups_sent=state.followups_sent)
    status = contact_status(state, waiting)
    db.upsert_contact(t["domain"], c.get("company"), c.get("contact_name"), c.get("contact_email"),
                      c.get("title"), status, state.outcome or None, state.followups_total)
    if not waiting:
        try:
            learn.from_thread(t["thread_id"], t["domain"], c, state.outcome, state.sentiment, log=log)
        except Exception as e:          # learning must never block the reply flow
            log(f"  learning skipped: {type(e).__name__}: {e}")
    return f"{'waiting again' if waiting else 'done'}: {state.outcome or 'awaiting reply'} (contact: {status})"


LOCK_ID = 7_312_026          # Postgres advisory lock: one reply check at a time (GitHub Actions or Vercel cron)


def main(trigger: str = "github") -> dict:
    """Run one reply check. Returns a short summary (used by the Vercel cron endpoint)."""
    if not db.enabled():
        raise SystemExit("NEON_DATABASE_URL is needed: conversations are stored there")
    # Advisory locks need a real session: use Neon's direct host, not the connection pooler.
    lock = psycopg.connect(config.NEON_DATABASE_URL.replace("-pooler.", "."), autocommit=True)
    try:
        if not lock.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
            log("another reply check is already running - skipping this one")
            return {"ran": False, "reason": "another check is running"}
        llm.on_usage = db.record_usage           # token use per call -> llm_usage table
        b = budget.status()
        if b["paused"]:
            log("PAUSED: " + budget.pause_message(b))
            return {"ran": False, "reason": "budget", "budget": b}
        now = dt.datetime.now(dt.timezone.utc)
        window = sendtime.best_window()
        log(f"reply check ({trigger}) - send window for follow-ups: {sendtime.describe(window)}")
        threads = db.awaiting_threads()
        log(f"{len(threads)} conversation(s) waiting for a reply")
        results = []
        for t in threads:
            log(f"\n=== {t['domain']} ({t['thread_id']}) - {t['subject']!r}")
            try:
                r = check_thread(t, now, window)
            except Exception as e:          # one bad thread must not stop the others
                r = f"ERROR {type(e).__name__}: {e} (will retry next run)"
            log(f"  -> {r}")
            results.append({"domain": t["domain"], "thread_id": t["thread_id"], "result": r})
        try:
            tuning = tune.run(log=log)             # at most once a day: adjust the rubric from outcomes
        except Exception as e:                    # tuning must never block the reply flow
            tuning = {"ran": False, "reason": f"{type(e).__name__}: {e}"}
            log(f"auto-tune skipped: {tuning['reason']}")
        return {"ran": True, "trigger": trigger, "window": sendtime.describe(window), "threads": results,
                "tuning": {k: tuning.get(k) for k in ("ran", "summary", "reason")}}
    finally:
        try:
            lock.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
        finally:
            lock.close()


if __name__ == "__main__":
    main()
