"""Sequence 1 reply handling + Sequence 2 (schedule the call), built as a LangGraph.

    Sequence 1 (every prospect):  the cold email went out -> read the first reply
        not_interested -> End
        no reply       -> End
        unclear        -> hand off to the Growth Lead
        interested     -> Sequence 2

    Sequence 2 (only if interested):
        check_calendar -> propose 2-3 times (send_email) -> WAIT for the reply
        confirms a time   -> book_meeting -> update_crm -> End
        counter-proposes  -> reply_round += 1, calendar_checked = False,
                             back to check_calendar, never re-offering a rejected slot;
                             hand off at MAX_REPLY_ROUNDS
        goes silent       -> hand off (no reply in the follow-up window)

Checkpointing: the graph pauses at `await_reply` with an interrupt and its state is saved
to SQLite (CHECKPOINT_DB). Resuming the same thread continues from the pause.

Retry table (per tool, decided before the build):
    check_calendar  transient -> retry 3x, then hand off (propose depends on it)
    send_email      transient -> retry 3x, then hand off (the proposal is needed)
    book_meeting    transient -> retry 3x; permanent (invite refused) -> abort
    update_crm      transient -> retry 3x (inside crm.py); still failing -> log by hand

Replies only reach this code as files (see replies.py), because the To: address is always
your own inbox. Dry runs (no --send) skip every outbound action but walk the same flow.
"""
import datetime as dt
import operator
import uuid
from dataclasses import asdict, dataclass, field
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import calendar_tool
import config
import crm
import replies
import sender
from retry import PermanentError, with_retry


@dataclass
class MeetingState:
    """Final record for the run report (the six fields from the lecture, plus outcome)."""
    reply_status: str = "no_reply"        # no_reply | interested | not_interested
    calendar_checked: bool = False
    times_proposed: list = field(default_factory=list)
    rejected_slots: list = field(default_factory=list)
    reply_round: int = 0
    meeting_status: str = "unscheduled"   # unscheduled | pending_confirmation | confirmed
    crm_logged: bool = False
    outcome: str = ""
    escalation: str = ""
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


class Sched(TypedDict):
    """Graph state. Every node reads it and returns only the fields it changes."""
    reply_status: str
    calendar_checked: bool
    times_proposed: list
    rejected_slots: list
    reply_round: int
    meeting_status: str
    crm_logged: bool
    outcome: str
    escalation: str
    booked_slot: str | None
    scheduling: bool                      # False = Sequence 1, True = Sequence 2
    last_reply: str | None
    intent: str
    chosen_index: int | None
    trace: Annotated[list, operator.add]


def _fmt(iso: str) -> str:
    d = dt.datetime.fromisoformat(iso)
    return d.strftime("%a %d %b %Y, %H:%M ") + d.tzname()


def _attempt(name: str, fn, attempts: int = 3):
    """One tool call under the retry table. Returns (ok, result_or_error, trace_line)."""
    try:
        res = with_retry(fn, attempts=attempts, base_delay=2.0, transient=(Exception,))
        return True, res, f"{name}: ok"
    except PermanentError as e:
        return False, str(e), f"{name}: permanent failure, not retried: {e}"
    except Exception as e:
        return False, str(e), f"{name}: failed after {attempts} tries: {e}"


def _ics(ctx: Ctx, iso: str) -> bytes:
    import uuid as _uuid
    start = dt.datetime.fromisoformat(iso).astimezone(dt.timezone.utc)
    end = start + dt.timedelta(minutes=20)
    fmt = "%Y%m%dT%H%M%SZ"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Sprinto outreach//EN", "METHOD:REQUEST",
             "BEGIN:VEVENT", f"UID:{_uuid.uuid4()}@sprinto-outreach",
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


def build_graph(ctx: Ctx) -> StateGraph:
    first = ctx.contact_name.split()[0]

    def await_reply(s):
        # Pause here until the prospect replies (or the follow-up window passes: None).
        answer = interrupt({"waiting_for": "prospect_reply", "offered_slots": s["times_proposed"]})
        # Silence is sent as a sentinel: LangGraph does not accept None as a resume value.
        if isinstance(answer, dict) and answer.get("silence"):
            return {"last_reply": None}
        return {"last_reply": answer}

    def read(s):
        if s["last_reply"] is None:
            return {"intent": "silence", "chosen_index": None}
        r = replies.classify(s["last_reply"], s["times_proposed"])
        return {"intent": r.intent, "chosen_index": r.chosen_slot_index}

    def start_scheduling(s):
        return {"scheduling": True, "reply_status": "interested"}

    def not_interested(s):
        return {"reply_status": "not_interested", "meeting_status": "unscheduled", "outcome": "not_interested"}

    def no_reply(s):
        return {"outcome": "no_reply"}

    def silent(s):
        return {"outcome": "escalated_to_growth_lead",
                "escalation": f"no reply within {config.FOLLOWUP_HOURS}h follow-up window",
                "trace": ["follow-up window passed, no reply"]}

    def unclear(s):
        where = "unclear reply during scheduling" if s["scheduling"] else "first reply unclear"
        return {"outcome": "escalated_to_growth_lead", "escalation": where}

    def counter(s):
        n = s["reply_round"] + 1
        upd = {"reply_round": n,
               "rejected_slots": s["rejected_slots"] + s["times_proposed"],
               "calendar_checked": False,
               "trace": [f"counter-proposal {n}"]}
        if n >= config.MAX_REPLY_ROUNDS:
            upd.update(outcome="escalated_to_growth_lead",
                       escalation=f"{n} counter-proposals, no agreed time")
        return upd

    def check_calendar(s):
        now = dt.datetime.now(dt.timezone.utc)
        excluded = set(s["rejected_slots"])
        ok, res, line = _attempt("check_calendar", lambda: calendar_tool.check_calendar(now, excluded))
        if not ok:
            return {"outcome": "escalated_to_growth_lead",
                    "escalation": f"check_calendar failed: {res}", "trace": [line]}
        if res["status"] != "free" or not res["slots"]:
            return {"outcome": "escalated_to_growth_lead",
                    "escalation": f"calendar {res['status']}, no slots to offer", "trace": [line]}
        return {"calendar_checked": True, "times_proposed": res["slots"], "trace": [line]}

    def propose(s):
        slots = s["times_proposed"]
        ctx.log(f"  proposed: {', '.join(_fmt(x) for x in slots)}")
        if not ctx.do_send:
            return {"meeting_status": "pending_confirmation", "trace": ["send_email: dry run, not sent"]}
        ok, err, line = _attempt("send_email", lambda: sender.send(
            "Re: call times", _propose_body(first, slots), ctx.contact_name, ctx.contact_email))
        if not ok:
            return {"outcome": "escalated_to_growth_lead",
                    "escalation": f"send_email failed: {err}", "trace": [line]}
        return {"meeting_status": "pending_confirmation", "trace": [line]}

    def book(s):
        slot = s["times_proposed"][s["chosen_index"]]
        if not ctx.do_send:
            ctx.log(f"  booked (dry run): {_fmt(slot)}")
            return {"booked_slot": slot, "meeting_status": "confirmed", "outcome": "booked",
                    "trace": ["book_meeting: dry run, invite not sent"]}

        def _send():
            sender.send(f"Invite: 20-min call, {_fmt(slot)}",
                        f"Hi {first},\n\nConfirmed: {_fmt(slot)}. The calendar invite is attached.\n\nSprinto",
                        ctx.contact_name, ctx.contact_email,
                        attachment=("invite.ics", _ics(ctx, slot), "text", "calendar"))
        ok, err, line = _attempt("book_meeting", _send)
        if not ok:
            return {"outcome": "aborted", "escalation": "book_meeting failed; invite not sent", "trace": [line]}
        ctx.log(f"  booked: {_fmt(slot)}")
        return {"booked_slot": slot, "meeting_status": "confirmed", "outcome": "booked", "trace": [line]}

    def finish(s):
        upd = {"trace": []}
        if s.get("escalation"):
            ctx.log(f"  HAND OFF to Growth Lead: {s['escalation']}")
        if not ctx.do_send:
            upd["trace"].append("update_crm: dry run, not written")
            return upd
        logged = crm.update_crm(ctx.domain, ctx.contact_name, s["outcome"], _record(s))
        upd["crm_logged"] = logged
        upd["trace"].append("update_crm: ok" if logged else "update_crm: failed after retries")
        if not logged:
            upd["escalation"] = (s.get("escalation") + "; " if s.get("escalation") else "") + \
                                "CRM write failed, log outcome by hand"
        return upd

    def route_read(s):
        if s["last_reply"] is None:
            return "silent" if s["scheduling"] else "no_reply"
        intent, idx = s["intent"], s["chosen_index"]
        if not s["scheduling"]:
            if intent == "not_interested":
                return "not_interested"
            if intent == "unclear":
                return "unclear"
            return "start_scheduling"
        if intent == "confirms_time" and idx is not None and 0 <= idx < len(s["times_proposed"]):
            return "book"
        if intent == "not_interested":
            return "not_interested"
        if intent in ("counter_proposes", "confirms_time"):
            return "counter"
        return "unclear"

    def done_or(next_node):
        return lambda s: "finish" if s["outcome"] else next_node

    g = StateGraph(Sched)
    for name, fn in [("await_reply", await_reply), ("read", read), ("start_scheduling", start_scheduling),
                     ("not_interested", not_interested), ("no_reply", no_reply), ("silent", silent),
                     ("unclear", unclear), ("counter", counter), ("check_calendar", check_calendar),
                     ("propose", propose), ("book", book), ("finish", finish)]:
        g.add_node(name, fn)

    g.add_edge(START, "await_reply")
    g.add_edge("await_reply", "read")
    g.add_conditional_edges("read", route_read, {
        "silent": "silent", "no_reply": "no_reply", "not_interested": "not_interested",
        "unclear": "unclear", "start_scheduling": "start_scheduling", "book": "book", "counter": "counter"})
    g.add_edge("start_scheduling", "check_calendar")
    g.add_conditional_edges("check_calendar", done_or("propose"), {"finish": "finish", "propose": "propose"})
    g.add_conditional_edges("propose", done_or("await_reply"), {"finish": "finish", "await_reply": "await_reply"})
    g.add_conditional_edges("counter", done_or("check_calendar"),
                            {"finish": "finish", "check_calendar": "check_calendar"})
    g.add_edge("book", "finish")
    for n in ("not_interested", "no_reply", "silent", "unclear"):
        g.add_edge(n, "finish")
    g.add_edge("finish", END)
    return g


def _record(s: dict) -> dict:
    keys = ["reply_status", "calendar_checked", "times_proposed", "rejected_slots", "reply_round",
            "meeting_status", "crm_logged", "outcome", "escalation", "booked_slot"]
    return {k: s.get(k) for k in keys} | {"trace": s.get("trace", [])}


def _initial() -> dict:
    return {"reply_status": "no_reply", "calendar_checked": False, "times_proposed": [],
            "rejected_slots": [], "reply_round": 0, "meeting_status": "unscheduled", "crm_logged": False,
            "outcome": "", "escalation": "", "booked_slot": None, "scheduling": False,
            "last_reply": None, "intent": "", "chosen_index": None, "trace": []}


def run(ctx: Ctx, reply_blocks: list[str]) -> MeetingState:
    """Drive the graph with the prospect's replies. Each pause at await_reply takes the next
    reply; when the list runs out, the graph gets None (silence) and hands off."""
    queue = list(reply_blocks)
    thread = {"configurable": {"thread_id": f"{ctx.domain}:{uuid.uuid4().hex[:8]}"}}
    with SqliteSaver.from_conn_string(config.CHECKPOINT_DB) as saver:
        graph = build_graph(ctx).compile(checkpointer=saver)
        graph.invoke(_initial(), thread)
        while graph.get_state(thread).next:          # paused, waiting for the prospect
            reply = queue.pop(0) if queue else {"silence": True}
            graph.invoke(Command(resume=reply), thread)
        values = graph.get_state(thread).values
    return MeetingState(**{k: values.get(k) for k in asdict(MeetingState()).keys()})
