"""Sequence 1 reply handling + Sequence 2 (schedule the call), per the L5 workflow.

    Sequence 1 (every prospect):  the email went out -> read the reply
        not_interested -> End
        no reply       -> End
        unclear        -> escalate to the Growth Lead
        interested     -> Sequence 2

    Sequence 2 (only if interested):
        check_calendar -> propose 2-3 times (send_email) -> wait for reply
        confirms a time   -> book_meeting -> update_crm -> End
        counter-proposes  -> reply_round += 1, calendar_checked = False,
                             back to check_calendar (never re-offer a rejected slot)
                             escalate at MAX_REPLY_ROUNDS
        goes silent       -> escalate after the follow-up window

The state object holds the six fields from the lecture and is the only memory
the loop uses. Retry table (per tool, decided before the build):
    check_calendar  transient -> retry 3x, then escalate (propose depends on it)
    send_email      transient -> retry 3x, then escalate (the proposal is needed)
    book_meeting    transient -> retry 3x; permanent (invite refused) -> abort
    update_crm      transient -> retry 3x; still failing -> escalate, log by hand
Iteration caps: MAX_REPLY_ROUNDS counter-proposals, then a human handoff.

Replies only reach this code as files (see replies.py), because the To: address
is always your own inbox. Dry runs (no --send) skip every outbound action but
still walk the flow, so the decisions can be checked before sending.
"""
import datetime as dt
import uuid
from dataclasses import asdict, dataclass, field
from zoneinfo import ZoneInfo

import calendar_tool
import config
import crm
import replies
import sender
from retry import PermanentError, with_retry


@dataclass
class MeetingState:
    reply_status: str = "no_reply"        # no_reply | interested | not_interested
    calendar_checked: bool = False
    times_proposed: list = field(default_factory=list)   # slots last sent
    rejected_slots: list = field(default_factory=list)   # never offered again
    reply_round: int = 0
    meeting_status: str = "unscheduled"   # unscheduled | pending_confirmation | confirmed
    crm_logged: bool = False
    outcome: str = ""                     # final result for the run report
    escalation: str = ""                  # why a human has to take over
    booked_slot: str | None = None
    trace: list = field(default_factory=list)


@dataclass
class Ctx:
    domain: str
    contact_name: str
    contact_email: str
    company: str
    sender_name: str
    do_send: bool
    log: callable = print


def _fmt(iso: str) -> str:
    d = dt.datetime.fromisoformat(iso)
    return d.strftime("%a %d %b %Y, %H:%M ") + d.tzname()


def _call(name: str, fn, state: MeetingState, attempts: int = 3):
    """Run one tool under the retry table. Returns (ok, result_or_error)."""
    try:
        res = with_retry(fn, attempts=attempts, base_delay=2.0, transient=(Exception,))
        state.trace.append(f"{name}: ok")
        return True, res
    except PermanentError as e:
        state.trace.append(f"{name}: permanent failure, not retried: {e}")
        return False, str(e)
    except Exception as e:
        state.trace.append(f"{name}: failed after {attempts} tries: {e}")
        return False, str(e)


def _escalate(ctx: Ctx, state: MeetingState, reason: str,
              outcome: str = "escalated_to_growth_lead") -> MeetingState:
    state.outcome = outcome
    state.escalation = reason
    ctx.log(f"  HAND OFF to Growth Lead: {reason}")
    return state


def _ics(ctx: Ctx, iso: str) -> bytes:
    start = dt.datetime.fromisoformat(iso).astimezone(dt.timezone.utc)
    end = start + dt.timedelta(minutes=20)
    fmt = "%Y%m%dT%H%M%SZ"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Sprinto outreach//EN", "METHOD:REQUEST",
             "BEGIN:VEVENT", f"UID:{uuid.uuid4()}@sprinto-outreach",
             f"DTSTAMP:{dt.datetime.now(dt.timezone.utc).strftime(fmt)}",
             f"DTSTART:{start.strftime(fmt)}", f"DTEND:{end.strftime(fmt)}",
             f"SUMMARY:Sprinto x {ctx.company}: 20-min call",
             f"ATTENDEE;CN={ctx.contact_name}:mailto:{ctx.contact_email}",
             "END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(lines) + "\r\n").encode()


def _propose_body(first: str, slots: list[str]) -> str:
    lines = [f"Hi {first},", "", "Thanks for getting back. Would any of these work for a 20-minute call?", ""]
    lines += [f"- {_fmt(s)}" for s in slots]
    lines += ["", "Reply with the one that suits you, or suggest another time."]
    return "\n".join(lines)


def _book(ctx: Ctx, state: MeetingState, slot: str) -> bool:
    first = ctx.contact_name.split()[0]
    body = f"Hi {first},\n\nConfirmed: {_fmt(slot)}. The calendar invite is attached.\n\nSprinto"

    def _send():
        if ctx.do_send:
            sender.send(f"Invite: 20-min call, {_fmt(slot)}", body, ctx.contact_name, ctx.contact_email,
                        attachment=("invite.ics", _ics(ctx, slot), "text", "calendar"))
        return True

    ok, err = _call("book_meeting", _send, state)
    if ok:
        state.booked_slot = slot
        state.meeting_status = "confirmed"
    return ok


def _finish(ctx: Ctx, state: MeetingState) -> MeetingState:
    if not ctx.do_send:
        state.trace.append("update_crm: dry run, not written")
        return state
    ok, err = _call("update_crm", lambda: crm.update_crm(ctx.domain, ctx.contact_name, state.outcome, asdict(state)), state)
    state.crm_logged = ok
    if not ok:
        state.escalation = (state.escalation + "; " if state.escalation else "") + "CRM write failed, log outcome by hand"
    return state


def run(ctx: Ctx, reply_blocks: list[str]) -> MeetingState:
    """Process the prospect's replies in order. An empty list means silence."""
    state = MeetingState()
    now = dt.datetime.now(dt.timezone.utc)

    # ---- Sequence 1: read the first reply to the cold email
    if not reply_blocks:
        state.outcome = "no_reply"
        return _finish(ctx, state)
    first = replies.classify(reply_blocks[0], [])
    ctx.log(f"  reply 1: {first.intent}")
    if first.intent == "not_interested":
        state.reply_status = "not_interested"
        state.outcome = "not_interested"
        return _finish(ctx, state)
    if first.intent == "unclear":
        return _finish(ctx, _escalate(ctx, state, "first reply unclear"))
    state.reply_status = "interested"    # interested / confirms_time / counter_proposes -> Sequence 2

    # ---- Sequence 2: schedule the call
    rest = list(reply_blocks[1:])
    while True:
        # check_calendar
        ok, res = _call("check_calendar",
                        lambda: calendar_tool.check_calendar(now, set(state.rejected_slots)), state)
        if not ok:
            return _finish(ctx, _escalate(ctx, state, f"check_calendar failed: {res}"))
        if res["status"] != "free" or not res["slots"]:
            return _finish(ctx, _escalate(ctx, state, f"calendar {res['status']}, no slots to offer"))
        state.calendar_checked = True
        slots = res["slots"]

        # send_email with the proposed times
        if ctx.do_send:
            ok, err = _call("send_email", lambda: sender.send(
                f"Re: call times", _propose_body(ctx.contact_name.split()[0], slots),
                ctx.contact_name, ctx.contact_email), state)
            if not ok:
                return _finish(ctx, _escalate(ctx, state, f"send_email failed: {err}"))
        else:
            state.trace.append("send_email: dry run, not sent")
        state.times_proposed = slots
        state.meeting_status = "pending_confirmation"
        ctx.log(f"  proposed: {', '.join(_fmt(s) for s in slots)}")

        # wait for the reply; no further reply = silence past the follow-up window
        if not rest:
            return _finish(ctx, _escalate(ctx, state, f"no reply within {config.FOLLOWUP_HOURS}h follow-up window"))
        read = replies.classify(rest.pop(0), slots)
        ctx.log(f"  reply: {read.intent}")

        if read.intent == "confirms_time" and read.chosen_slot_index is not None \
                and 0 <= read.chosen_slot_index < len(slots):
            chosen = slots[read.chosen_slot_index]
            if not _book(ctx, state, chosen):
                return _finish(ctx, _escalate(ctx, state, "book_meeting failed; invite not sent",
                                              outcome="aborted"))
            state.outcome = "booked"
            ctx.log(f"  booked: {_fmt(chosen)}")
            return _finish(ctx, state)

        if read.intent == "not_interested":
            state.reply_status = "not_interested"
            state.meeting_status = "unscheduled"
            state.outcome = "not_interested"
            return _finish(ctx, state)

        if read.intent in ("counter_proposes", "confirms_time"):
            state.reply_round += 1
            state.rejected_slots += slots
            if state.reply_round >= config.MAX_REPLY_ROUNDS:
                return _finish(ctx, _escalate(ctx, state, f"{state.reply_round} counter-proposals, no agreed time"))
            state.calendar_checked = False      # loop back to check_calendar
            continue

        return _finish(ctx, _escalate(ctx, state, "unclear reply during scheduling"))
