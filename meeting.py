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

Replies reach this code in one of two ways (REPLY_MODE):
    file  - run(): all replies from REPLIES_DIR/<domain>.txt in one go (tests and demos)
    gmail - start() after the cold email, then check_replies.py calls resume() for each
            real reply found in your inbox (inbox.py). Checkpoints are kept in Neon.
Dry runs (no --send) skip every outbound action but walk the same flow.
"""
import datetime as dt
import operator
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Annotated, TypedDict
from zoneinfo import ZoneInfo

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import calendar_tool
import config
import crm
import replies
import sender
import writer
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
    subject: str = ""                     # cold email subject; follow-ups reply in that thread
    in_reply_to: str | None = None        # Message-ID of the reply being answered (Gmail mode)


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
    proposed_start: str | None            # a specific time the prospect named (ISO), if any
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


def _attendees(ctx: Ctx) -> list[str]:
    """Build 1: the inbox that receives the invite (SEND_TO) must be a guest, or Google Calendar
    shows no Yes / No and never adds the event. The prospect is listed as the intended guest.
    One line per address (SEND_TO may be the stand-in contact's own address)."""
    people = [("You", config.SEND_TO), (ctx.contact_name, ctx.contact_email)]
    seen, lines = set(), []
    for name, addr in people:
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            lines.append(f"ATTENDEE;CN={name};ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{addr}")
    return lines


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
             f"ORGANIZER;CN={ctx.sender_name}:mailto:{config.GMAIL_ADDRESS}",
             *_attendees(ctx),
             "STATUS:CONFIRMED",
             "END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(lines) + "\r\n").encode()


def _sign_off(sender_name: str) -> str:
    # "Ravi Bussareddy, Sprinto" -> name and company on separate lines, as in the cold email
    return "Kind regards,\n" + sender_name.replace(", ", "\n")


def _propose_body(first: str, slots: list[str], sender_name: str) -> str:
    lines = [writer.greeting(first), "", "Thank you for coming back to me. Would any of these times "
             "suit you for a 20-minute call?", ""]
    lines += [f"- {_fmt(s)}" for s in slots]
    lines += ["", "Just reply with the one that works best, or suggest another time.", "",
              _sign_off(sender_name)]
    return "\n".join(lines)


def _invite_body(first: str, slot: str, sender_name: str) -> str:
    return (f"{writer.greeting(first)}\n\nThank you - that's confirmed for {_fmt(slot)}. "
            f"The calendar invite is attached.\n\nLooking forward to speaking with you.\n\n"
            f"{_sign_off(sender_name)}")


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
            return {"intent": "silence", "chosen_index": None, "proposed_start": None}
        r = replies.classify(s["last_reply"], s["times_proposed"])
        proposed = None
        if r.proposed_start:
            try:
                d = dt.datetime.fromisoformat(r.proposed_start)
                proposed = (d if d.tzinfo else d.replace(tzinfo=ZoneInfo(config.SLOT_TIMEZONE))).isoformat()
            except ValueError:
                proposed = None          # unreadable time: fall back to offering slots
        return {"intent": r.intent, "chosen_index": r.chosen_slot_index, "proposed_start": proposed}

    def try_proposed(s):
        """The prospect named a time: book it if the calendar is free, else offer alternatives."""
        iso = s["proposed_start"]
        now = dt.datetime.now(dt.timezone.utc)
        ok, res, line = _attempt("check_calendar",
                                 lambda: calendar_tool.slot_free(dt.datetime.fromisoformat(iso), now))
        base = {"scheduling": True, "reply_status": "interested"}
        if ok and res["status"] == "free":
            ctx.log(f"  their time {_fmt(iso)} is free -> booking it")
            return base | {"calendar_checked": True, "times_proposed": [iso], "chosen_index": 0,
                           "trace": [f"{line}; their proposed time {iso} is free"]}
        why = res["status"] if ok else f"calendar error: {res}"
        ctx.log(f"  their time {_fmt(iso)} not possible ({why}) -> offering alternatives")
        return base | {"calendar_checked": False, "chosen_index": None,
                       "rejected_slots": s["rejected_slots"] + [iso],
                       "trace": [f"{line}; their proposed time {iso}: {why}"]}

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
        subject = f"Re: {ctx.subject}" if ctx.subject else "Re: call times"
        ok, err, line = _attempt("send_email", lambda: sender.send(
            subject, _propose_body(first, slots, ctx.sender_name), ctx.contact_name, ctx.contact_email,
            in_reply_to=ctx.in_reply_to))
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
                        _invite_body(first, slot, ctx.sender_name),
                        ctx.contact_name, ctx.contact_email,
                        attachment=("invite.ics", _ics(ctx, slot), "text", "calendar"),
                        in_reply_to=ctx.in_reply_to)
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
        picked_offered = intent == "confirms_time" and idx is not None and 0 <= idx < len(s["times_proposed"])
        if s.get("proposed_start") and not picked_offered and \
                intent in ("interested", "counter_proposes", "confirms_time"):
            return "try_proposed"
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
    for name, fn in [("await_reply", await_reply), ("read", read), ("try_proposed", try_proposed),
                     ("start_scheduling", start_scheduling),
                     ("not_interested", not_interested), ("no_reply", no_reply), ("silent", silent),
                     ("unclear", unclear), ("counter", counter), ("check_calendar", check_calendar),
                     ("propose", propose), ("book", book), ("finish", finish)]:
        g.add_node(name, fn)

    g.add_edge(START, "await_reply")
    g.add_edge("await_reply", "read")
    g.add_conditional_edges("read", route_read, {
        "silent": "silent", "no_reply": "no_reply", "not_interested": "not_interested",
        "unclear": "unclear", "start_scheduling": "start_scheduling", "book": "book", "counter": "counter",
        "try_proposed": "try_proposed"})
    # Their time is free -> book it; otherwise offer alternatives (counts as a counter round).
    g.add_conditional_edges("try_proposed", lambda s: "book" if s["chosen_index"] == 0 else "counter",
                            {"book": "book", "counter": "counter"})
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
            "last_reply": None, "intent": "", "chosen_index": None, "proposed_start": None, "trace": []}


@contextmanager
def _saver(persistent: bool):
    """Neon (Postgres) checkpoints when the conversation must survive between runs
    (Gmail mode, where a reply can arrive hours later); local SQLite otherwise."""
    if persistent:
        from langgraph.checkpoint.postgres import PostgresSaver
        with PostgresSaver.from_conn_string(config.NEON_DATABASE_URL) as s:
            s.setup()
            yield s
    else:
        with SqliteSaver.from_conn_string(config.CHECKPOINT_DB) as s:
            yield s


def _snapshot(graph, thread) -> tuple[MeetingState, bool]:
    snap = graph.get_state(thread)
    values = snap.values
    state = MeetingState(**{k: values.get(k) for k in asdict(MeetingState()).keys()})
    return state, bool(snap.next)                   # True = paused, waiting for the prospect


def start(ctx: Ctx, persistent: bool = True) -> tuple[str, MeetingState, bool]:
    """Begin a conversation after the cold email went out: runs until the first pause."""
    thread_id = f"{ctx.domain}:{uuid.uuid4().hex[:8]}"
    thread = {"configurable": {"thread_id": thread_id}}
    with _saver(persistent) as saver:
        graph = build_graph(ctx).compile(checkpointer=saver)
        graph.invoke(_initial(), thread)
        state, waiting = _snapshot(graph, thread)
    return thread_id, state, waiting


def resume(ctx: Ctx, thread_id: str, reply: str | None, persistent: bool = True) -> tuple[MeetingState, bool]:
    """Continue a saved conversation with one reply (None = the follow-up window passed)."""
    thread = {"configurable": {"thread_id": thread_id}}
    with _saver(persistent) as saver:
        graph = build_graph(ctx).compile(checkpointer=saver)
        graph.invoke(Command(resume=reply if reply is not None else {"silence": True}), thread)
        return _snapshot(graph, thread)


def run(ctx: Ctx, reply_blocks: list[str]) -> MeetingState:
    """File mode: drive the graph with all replies at once. Each pause at await_reply takes
    the next reply; when the list runs out, the graph gets silence and hands off."""
    queue = list(reply_blocks)
    thread = {"configurable": {"thread_id": f"{ctx.domain}:{uuid.uuid4().hex[:8]}"}}
    with _saver(False) as saver:
        graph = build_graph(ctx).compile(checkpointer=saver)
        graph.invoke(_initial(), thread)
        while graph.get_state(thread).next:          # paused, waiting for the prospect
            reply = queue.pop(0) if queue else {"silence": True}
            graph.invoke(Command(resume=reply), thread)
        state, _ = _snapshot(graph, thread)
    return state
