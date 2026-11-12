"""Offline tests for the reply flow. No LLM, no email, no database, no network.
Run from the repo root:  PYTHONPATH=. python3 tests/test_sequence2.py"""
import datetime as dt
import json
import os
import tempfile

import config
import crm
import meeting
import replies
import retry
import sender
from calendar_tool import check_calendar
from retry import PermanentError
from replies import ReplyRead

retry.time.sleep = lambda s: None          # no back-off delay in tests

SENT = []
CRM = []


def fake_send(subject, body, name, email, attachment=None):
    SENT.append((subject, attachment is not None))
    return config.SEND_TO


sender.send = fake_send
crm.update_crm = lambda d, c, o, r: CRM.append(o) or True


def make_busy(intervals):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(intervals, f)
    f.close()
    return f.name


def classifier(script):
    """Return a classify() stub that answers from a script of intents."""
    items = list(script)

    def _classify(reply, slots):
        intent, idx = items.pop(0)
        return ReplyRead(intent=intent, chosen_slot_index=idx, proposed_times=[])
    return _classify


def ctx(do_send=False):
    return meeting.Ctx(domain="acme.io", contact_name="Asha Rao", contact_email="asha@acme.io",
                       company="Acme", sender_name="Growth", do_send=do_send, log=lambda m: None)


def run_case(script, replies_list, busy=None, do_send=False):
    config.CALENDAR_BUSY_FILE = busy if busy is not None else make_busy([])
    replies.classify = classifier(script)
    SENT.clear(); CRM.clear()
    return meeting.run(ctx(do_send), replies_list)


def test_no_reply():
    s = run_case([], [])
    assert s.outcome == "no_reply", s.outcome


def test_not_interested_ends_at_sequence_1():
    s = run_case([("not_interested", None)], ["not interested, thanks"])
    assert s.outcome == "not_interested" and s.calendar_checked is False


def test_unclear_first_reply_escalates():
    s = run_case([("unclear", None)], ["out of office"])
    assert s.outcome == "escalated_to_growth_lead" and "unclear" in s.escalation


def test_interested_then_confirms_books_and_logs():
    s = run_case([("interested", None), ("confirms_time", 1)], ["yes, interested", "the 3pm one works"], do_send=True)
    assert s.outcome == "booked", (s.outcome, s.escalation)
    assert s.booked_slot == s.times_proposed[1]
    assert s.meeting_status == "confirmed" and s.reply_status == "interested"
    assert [a for _, a in SENT] == [False, True]          # proposal email, then invite with .ics
    assert CRM == ["booked"]


def test_dry_run_sends_nothing_and_writes_no_crm():
    s = run_case([("interested", None), ("confirms_time", 0)], ["yes", "first one"], do_send=False)
    assert s.outcome == "booked"
    assert SENT == [] and CRM == []


def test_counter_proposal_loops_back_and_excludes_rejected_slots():
    s = run_case([("interested", None), ("counter_proposes", None), ("confirms_time", 0)],
                 ["yes", "can we do Friday?", "Friday works"], do_send=True)
    assert s.outcome == "booked", (s.outcome, s.escalation)
    assert s.reply_round == 1
    assert not set(s.rejected_slots) & set(s.times_proposed)   # new offer never repeats a rejected time


def test_two_counter_proposals_hand_off_to_growth_lead():
    s = run_case([("interested", None), ("counter_proposes", None), ("counter_proposes", None)],
                 ["yes", "not that", "not that either"], do_send=True)
    assert s.outcome == "escalated_to_growth_lead" and s.reply_round == 2, (s.outcome, s.reply_round)


def test_silence_after_proposal_hands_off():
    s = run_case([("interested", None)], ["yes"], do_send=True)
    assert s.outcome == "escalated_to_growth_lead" and "follow-up window" in s.escalation, (s.outcome, s.escalation)


def test_no_calendar_access_hands_off_before_proposing():
    s = run_case([("interested", None)], ["yes"], busy="")
    assert s.outcome == "escalated_to_growth_lead" and "no-calendar-access" in s.escalation
    assert SENT == []


def test_transient_calendar_error_is_retried_then_succeeds():
    calls = {"n": 0}
    import calendar_tool
    real = calendar_tool.check_calendar

    def flaky(now, exclude=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("temporary read error")
        return real(now, exclude)
    calendar_tool.check_calendar = flaky
    try:
        s = run_case([("interested", None), ("confirms_time", 0)], ["yes", "ok"], do_send=True,
                     busy=make_busy([]))
    finally:
        calendar_tool.check_calendar = real
    assert calls["n"] == 3 and s.outcome == "booked", (calls, s.outcome)


def test_permanent_booking_failure_aborts_with_escalation():
    def refused(*a, **k):
        raise PermanentError("invite refused by server")
    real = sender.send
    sender.send = lambda subject, body, name, email, attachment=None: (
        refused() if attachment else real(subject, body, name, email, attachment))
    try:
        s = run_case([("interested", None), ("confirms_time", 0)], ["yes", "ok"], do_send=True)
    finally:
        sender.send = real
    assert s.outcome == "aborted" and s.meeting_status != "confirmed" and "book_meeting" in s.escalation, s.outcome


def test_calendar_returns_only_business_hours_future_slots():
    now = dt.datetime(2026, 11, 13, 6, 0, tzinfo=dt.timezone.utc)   # Fri, 11:30 IST
    res = check_calendar(now)
    assert res["status"] == "free" and len(res["slots"]) == config.SLOTS_TO_OFFER
    for iso in res["slots"]:
        d = dt.datetime.fromisoformat(iso)
        assert d.weekday() < 5 and d.hour in config.SLOT_HOURS


if __name__ == "__main__":
    config.REPLIES_DIR = "replies"
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except Exception as e:
            failed += 1
            print("FAIL", t.__name__, "-", repr(e))
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
