"""Continue every conversation that is waiting for a reply (REPLY_MODE=gmail).

    python check_replies.py

For each thread with status 'awaiting' in Neon:
- look in Gmail for new replies to that cold email (inbox.py)
- feed each one to the saved LangGraph conversation, oldest first: an interested
  reply gets proposed times, a picked time gets a calendar invite, and so on
- no reply for FOLLOWUP_HOURS since our last email -> the next follow-up (up to
  MAX_FOLLOWUPS, each with a new angle), then the contact becomes non_respondent
- a declined reply is thanked and the contact becomes a cold lead
- when a conversation ends, learn.py turns it into lessons for the feedback repo
Runs on a schedule in GitHub Actions; safe to run as often as you like, because
replies already handled are remembered by Message-ID.
"""
import datetime as dt

import config
import db
import inbox
import learn
import llm
import meeting


def log(msg: str):
    print(msg, flush=True)


# Where each contact stands once a conversation moves (contacts table, dashboard).
STATUS_BY_OUTCOME = {"booked": "meeting_booked", "not_interested": "cold_lead",
                     "non_respondent": "non_respondent", "no_reply": "non_respondent"}


def contact_status(state, waiting: bool) -> str:
    if not waiting:
        return STATUS_BY_OUTCOME.get(state.outcome, "escalated")
    return "engaged" if state.sentiment == "positive" else "contacted"


def check_thread(t: dict, now: dt.datetime) -> str:
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
    if not found and now - t["last_outbound_at"] > dt.timedelta(hours=config.FOLLOWUP_HOURS):
        log(f"  no reply within {config.FOLLOWUP_HOURS}h of our last email")
        ctx.in_reply_to = None
        state, waiting = meeting.resume(ctx, t["thread_id"], None)   # next follow-up, or non-respondent
        touched = True
    if state is None:
        return "no new reply"
    db.update_thread(t["thread_id"], "awaiting" if waiting else "done", state.outcome or "awaiting_reply",
                     processed, touched_outbound=touched, replied=state.replied,
                     sentiment=state.sentiment, followups_sent=state.followups_sent)
    status = contact_status(state, waiting)
    db.upsert_contact(t["domain"], c.get("company"), c.get("contact_name"), c.get("contact_email"),
                      c.get("title"), status, state.outcome or None, state.followups_sent)
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
    now = dt.datetime.now(dt.timezone.utc)
    threads = db.awaiting_threads()
    log(f"{len(threads)} conversation(s) waiting for a reply")
    for t in threads:
        log(f"\n=== {t['domain']} ({t['thread_id']}) - {t['subject']!r}")
        try:
            log(f"  -> {check_thread(t, now)}")
        except Exception as e:          # one bad thread must not stop the others
            log(f"  ERROR {type(e).__name__}: {e} (will retry next run)")


if __name__ == "__main__":
    main()
