"""End-to-end scenario runs: real scraping and real Gemini calls, with a FAKE test contact.

What is real:   scraping, extraction, qualification, pain points, email drafting, reply classification.
What is fake:   the PoC (a placeholder contact at example.invalid), email sending (recorded, not sent),
                CRM writes (recorded, not written to Neon), and the calendar (a temp busy file).

Run from the repo root:   PYTHONPATH=. python3 tests/e2e_sequence.py [domain]
"""
import json
import os
import sys
import tempfile

import config
import crm
import meeting
import pipeline
import poc as poc_mod
import replies
import sender

DOMAIN = sys.argv[1] if len(sys.argv) > 1 else "atlan.com"
FAKE_CONTACT = {"first_name": "Test", "last_name": "Contact", "email": "test@example.invalid",
                "title": "CTO (TEST STUB)", "source": "test stub", "why": "placeholder for end-to-end test"}

SCENARIOS = {
    "A_interested_then_confirms": ["Yes, interested. What does this look like?", "The first one works, thanks."],
    "B_counter_then_confirms": ["Interested. Can we talk?", "Friday is better for me.", "Yes, the second one works."],
    "C_interested_then_silence": ["Sounds interesting, tell me more."],
    "D_not_interested": ["Not interested, please remove me."],
    "E_unclear_first_reply": ["Out of office until Monday."],
}

SENT, CRM = [], []


def fake_send(subject, body, name, email, attachment=None):
    SENT.append({"subject": subject, "attachment": bool(attachment)})
    return config.SEND_TO


def fake_crm(domain, contact, outcome, record):
    CRM.append({"outcome": outcome, "escalation": record.get("escalation", "")})
    return True


poc_mod.find_poc = lambda domain, size: dict(FAKE_CONTACT)
sender.send = fake_send
crm.update_crm = fake_crm


def run_scenario(name, reply_blocks, busy_intervals=None):
    tmp = tempfile.mkdtemp()
    config.REPLIES_DIR = tmp
    with open(os.path.join(tmp, f"{DOMAIN}.txt"), "w") as f:
        f.write("\n---\n".join(reply_blocks))
    busy = os.path.join(tmp, "busy.json")
    with open(busy, "w") as f:
        json.dump(busy_intervals or [], f)
    config.CALENDAR_BUSY_FILE = "" if name.startswith("F_") else busy
    SENT.clear(); CRM.clear()
    lines = []
    rec = pipeline.process(DOMAIN, do_send=True, sender_name="Growth Team, Sprinto")
    # pipeline.process logs through print; we print a summary of the run record instead
    m = rec.get("meeting") or {}
    print(f"\n[{name}]")
    print(f"  decision: {rec.get('qualification', {}).get('decision')}   status: {rec.get('status')}")
    print(f"  outcome: {m.get('outcome')}   escalation: {m.get('escalation') or '-'}   booked: {m.get('booked_slot') or '-'}")
    print(f"  steps: {'; '.join(m.get('trace', [])) or '-'}")
    print(f"  emails recorded: {len(SENT)} (invites: {sum(1 for s in SENT if s['attachment'])})   CRM recorded: {[c['outcome'] for c in CRM]}")
    return rec


if __name__ == "__main__":
    results = {}
    for name, blocks in SCENARIOS.items():
        results[name] = run_scenario(name, blocks)
    # No calendar access: same replies as scenario A, but no busy file configured
    results["F_no_calendar_access"] = run_scenario("F_no_calendar_access", SCENARIOS["A_interested_then_confirms"])
    ok = sum(1 for r in results.values() if r.get("status"))
    print(f"\n{ok}/{len(results)} scenarios completed (see each status above)")
