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

import budget
import config
import db
import inbox
import learn
import llm
import meeting
import rubric
import sendtime


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
        return f"no new reply; next step on {due:%a %d %b %H:%M} UTC" + (f" (follow-up {n + 1})" if n < config.MAX_FOLLOWUPS else " (close)")
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


def main():
    if not db.enabled():
        raise SystemExit("NEON_DATABASE_URL is needed: conversations are stored there")
    llm.on_usage = db.record_usage           # token use per call -> llm_usage table
    b = budget.status()
    if b["paused"]:
        log("PAUSED: " + budget.pause_message(b))
        return
    now = dt.datetime.now(dt.timezone.utc)
    window = sendtime.best_window()
    log(f"send window for follow-ups: {sendtime.describe(window)}")
    threads = db.awaiting_threads()
    log(f"{len(threads)} conversation(s) waiting for a reply")
    for t in threads:
        log(f"\n=== {t['domain']} ({t['thread_id']}) - {t['subject']!r}")
        try:
            log(f"  -> {check_thread(t, now, window)}")
        except Exception as e:          # one bad thread must not stop the others
            log(f"  ERROR {type(e).__name__}: {e} (will retry next run)")


if __name__ == "__main__":
    main()
