"""Continue every conversation that is waiting for a reply (REPLY_MODE=gmail).

    python check_replies.py

For each thread with status 'awaiting' in Neon:
- look in Gmail for new replies to that cold email (inbox.py)
- feed each one to the saved LangGraph conversation, oldest first: an interested
  reply gets proposed times, a picked time gets a calendar invite, and so on
- no reply for FOLLOWUP_HOURS since our last email -> the silence path (hand-off)
Runs on a schedule in GitHub Actions; safe to run as often as you like, because
replies already handled are remembered by Message-ID.
"""
import datetime as dt

import config
import db
import inbox
import meeting


def log(msg: str):
    print(msg, flush=True)


def check_thread(t: dict, now: dt.datetime) -> str:
    ctx = meeting.Ctx(**t["ctx"], do_send=True, log=log, subject=t["subject"])
    processed = list(t["processed_ids"] or [])
    found = inbox.find_replies(t["subject"], t["created_at"], set(processed))
    state, waiting, answered = None, True, False
    for r in found:
        log(f"  reply from {r['from']} at {r['date']:%d %b %H:%M}: {r['text'][:120]!r}")
        ctx.in_reply_to = r["id"]
        state, waiting = meeting.resume(ctx, t["thread_id"], r["text"])
        processed.append(r["id"])
        answered = True
        if not waiting:
            break
    if not found and now - t["last_outbound_at"] > dt.timedelta(hours=config.FOLLOWUP_HOURS):
        log(f"  no reply within {config.FOLLOWUP_HOURS}h -> silence path")
        state, waiting = meeting.resume(ctx, t["thread_id"], None)
    if state is None:
        return "no new reply"
    db.update_thread(t["thread_id"], "awaiting" if waiting else "done", state.outcome or "awaiting_reply",
                     processed, touched_outbound=answered)
    return f"{'waiting again' if waiting else 'done'}: {state.outcome or 'times proposed'}"


def main():
    if not db.enabled():
        raise SystemExit("NEON_DATABASE_URL is needed: conversations are stored there")
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
