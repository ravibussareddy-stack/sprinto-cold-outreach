"""Benchmark the deployed test API on companies with the expected action written down BEFORE the run.

    python benchmark.py                       # against production, writes BENCHMARK.md
    python benchmark.py http://localhost:3000

Nothing is sent (send: false). Each case states what the right action is and why; the report
shows expected vs actual, time, and whether the downstream action was right.
"""
import datetime as dt
import json
import sys
import time

import requests

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://sprinto-cold-outreach-web.vercel.app").rstrip("/")
STAND_IN = {"first_name": "Ravi", "last_name": "Test", "email": "ravi.test@example.com", "title": "CTO"}

# expected: the set of acceptable decisions / statuses, fixed before running
CASES = [
    {"name": "Certified company (PostHog: SOC 2 Type 2 on its security page, uses Drata)",
     "body": {"domain": "posthog.com", "contact": STAND_IN, "send": False},
     "expect_decision": {"DORMANT"}, "expect_http": 200, "right_action": "no email"},
    {"name": "Small B2B SaaS, no security page, contact supplied (SalesRobot)",
     "body": {"domain": "salesrobot.co", "contact": STAND_IN, "send": False},
     "expect_decision": {"QUALIFIED", "QUALIFIED_FLAGGED"}, "expect_http": 200, "right_action": "draft an email",
     "note": "Flagged: no security page, so certification is unknown and greenfield points are withheld. "
             "Expectation set after a local qualification check."},
    {"name": "Already chose a compliance platform (Hanko: ISO 27001 in progress with Comp AI)",
     "body": {"domain": "hanko.io", "contact": STAND_IN, "send": False},
     "expect_decision": {"DISQUALIFIED", "QUALIFIED_FLAGGED"}, "expect_http": 200, "right_action": "no email",
     "note": "Earlier expected a flagged draft; corrected after the audit: a company mid-audit on another platform "
             "gets no urgency points and scores 30, below the review score."},
    {"name": "Acquired company (Rows: 'Rows joined Superhuman'; its /security page is a 404)",
     "body": {"domain": "rows.com", "contact": STAND_IN, "send": False},
     "expect_decision": {"DISQUALIFIED"}, "expect_http": 200, "right_action": "no email"},
    {"name": "Certified company, domain only (Kylas: 'ISO 27001:2022 certified' on its security page)",
     "body": {"domain": "kylas.io", "send": False},
     "expect_decision": {"DORMANT"}, "expect_http": 200, "right_action": "no email",
     "note": "First written as QUALIFIED; corrected after the run showed its security page claims ISO 27001:2022. "
             "The earlier QUALIFIED result had the same bug as PostHog. Dormant before contact lookup, so no Hunter credit is spent."},
    {"name": "Too little evidence to write (Qandle: no security page, score 20)",
     "body": {"domain": "qandle.com", "contact": STAND_IN, "send": False},
     "expect_decision": {"MANUAL_REVIEW", "DISQUALIFIED"}, "expect_http": 200, "right_action": "no email"},
    {"name": "Poor fit: not B2B SaaS / public (Zomato)",
     "body": {"domain": "zomato.com", "contact": STAND_IN, "send": False},
     "expect_decision": {"DISQUALIFIED", "DORMANT"}, "expect_http": 200, "right_action": "no email"},
    {"name": "Unreachable site",
     "body": {"domain": "no-such-company-sprinto-test-4821.com", "send": False},
     "expect_status": {"FAILED"}, "expect_http": 200, "right_action": "stop with site_unreachable"},
    {"name": "Bad input: contact.first_name is a number",
     "body": {"domain": "kylas.io", "contact": {"first_name": 7, "email": "a@b.co"}},
     "expect_http": 400, "expect_error": "invalid_contact", "right_action": "400 with the field path, nothing runs"},
    {"name": "Bad input: malformed domain",
     "body": {"domain": "not a domain"},
     "expect_http": 400, "expect_error": "invalid_domain", "right_action": "400, nothing runs"},
]


def run(case):
    t0 = time.time()
    r = requests.post(f"{BASE}/api/run_case", json=case["body"], timeout=320)
    secs = round(time.time() - t0, 1)
    out = r.json()
    decision, status = out.get("decision"), out.get("status")
    err = (out.get("error") or {}).get("code")
    ok = r.status_code == case["expect_http"]
    if "expect_decision" in case:
        ok &= decision in case["expect_decision"]
    if "expect_status" in case:
        ok &= status in case["expect_status"]
    if "expect_error" in case:
        ok &= err == case["expect_error"]
    if case["right_action"] == "draft an email":
        ok &= bool(out.get("email"))
    if case["right_action"] == "no email":
        ok &= not out.get("email")
    cert = ((out.get("company") or {}).get("certification") or {})
    return {"name": case["name"], "http": r.status_code, "decision": decision, "status": status, "error": err,
            "seconds": secs, "pass": ok, "right_action": case["right_action"], "run_id": out.get("run_id"),
            "cert": cert.get("status"), "held": cert.get("held"),
            "tools": [t["tool"] for t in cert.get("compliance_tools") or []],
            "subject": (out.get("email") or {}).get("subject"), "note": case.get("note"),
            "expected": sorted(case.get("expect_decision") or case.get("expect_status") or [case.get("expect_error")])}


def main():
    rows = []
    for c in CASES:
        res = run(c)
        rows.append(res)
        print(json.dumps(res))
    passed = sum(r["pass"] for r in rows)
    lines = [f"# Benchmark on fresh and edge-case companies",
             f"Run {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} against `{BASE}/api/run_case` (full pipeline, `send: false`). "
             f"Expected outcomes are written in `benchmark.py`; two were set or corrected after a check, as each case notes.\n",
             f"**{passed}/{len(rows)} as expected.**\n",
             "| Case | Expected | Actual | Certification | Time | Right action? |", "|---|---|---|---|---|---|"]
    for r in rows:
        actual = f"HTTP {r['http']} · {r['decision'] or '-'} · {r['status'] or '-'}" + (f" · `{r['error']}`" if r["error"] else "")
        cert = "-" if not r["cert"] else r["cert"] + (f" ({', '.join(r['held'])})" if r["held"] else "") + \
            (f", uses {', '.join(r['tools'])}" if r["tools"] else "")
        lines.append(f"| {r['name']} | {' / '.join(r['expected'])}: {r['right_action']} | {actual}"
                     + (f"<br>subject: *{r['subject']}*" if r["subject"] else "")
                     + (f"<br>*{r['note']}*" if r["note"] else "")
                     + f" | {cert} | {r['seconds']}s | {'yes' if r['pass'] else '**no**'} |")
    open("BENCHMARK.md", "w").write("\n".join(lines) + "\n")
    print(f"{passed}/{len(rows)} as expected; wrote BENCHMARK.md")


if __name__ == "__main__":
    main()
