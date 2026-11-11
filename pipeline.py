"""Sprinto cold outreach - end-to-end pipeline.

    python pipeline.py domains.txt              # research + draft, no send (dry run)
    python pipeline.py domains.txt --send       # also send to YOUR inbox
    python pipeline.py --domain acme.io --send  # single company

For every domain: scrape -> extract -> qualify (agent loop) -> find PoC ->
pain points -> draft email -> send. Failures are logged and the run moves on.
Every decision is written to runs/<timestamp>/ so it can be defended later.
"""
import argparse
import datetime as dt
import json
import os
import time

import config
import llm
import poc as poc_mod
import sender
import writer
from qualify import qualify


def log(msg):
    print(msg, flush=True)


def process(domain: str, do_send: bool, sender_name: str) -> dict:
    t0 = time.time()
    rec = {"domain": domain, "status": None}
    log(f"\n=== {domain} ===")

    # Steps 1-3: scrape, extract, qualify (with loop)
    try:
        q = qualify(domain, log)
    except Exception as e:  # degrade gracefully, never kill the batch
        rec.update(status="ERROR", error=f"qualify: {e}")
        return rec
    rec["qualification"] = {k: v for k, v in q.items() if k not in ("profile", "pages")}
    rec["profile"] = q["profile"].model_dump() if q["profile"] else None
    rec["pages_read"] = [p["url"] for p in q["pages"]]
    log(f"  decision: {q['decision']} - {q.get('reason')}")
    for r in q.get("score_reasons", []):
        log(f"    {r}")
    if q["decision"] not in ("QUALIFIED", "QUALIFIED_FLAGGED"):
        rec["status"] = q["decision"]
        return rec

    # Step 4: PoC
    profile = q["profile"]
    contact = poc_mod.find_poc(domain, profile.employee_estimate)
    rec["poc"] = contact
    if "error" in contact:
        log(f"  PoC: {contact['error']}")
        rec["status"] = "NO_POC"
        return rec
    log(f"  PoC: {contact['first_name']} {contact['last_name']} ({contact['title']}) via {contact['source']}")

    # Step 5: pain points
    pains = writer.pain_points(profile, contact)
    rec["pain_points"] = pains.model_dump()
    for p in pains.pain_points:
        log(f"  pain: {p.title}")

    # Step 6: email
    draft, problems = writer.draft_email(profile, contact, pains, sender_name)
    rec["email"] = draft.model_dump()
    rec["email_check_failures"] = problems
    log(f"  subject: {draft.subject}\n" + "\n".join("    " + l for l in draft.body.splitlines()))

    # Step 7: send (to your own inbox only)
    if problems:
        rec["status"] = "DRAFT_FAILED_CHECKS"
        log(f"  not sending - failed checks: {problems}")
    elif do_send:
        to = sender.send(draft.subject, draft.body,
                         f"{contact['first_name']} {contact['last_name']}", contact["email"])
        rec["status"] = "SENT"
        log(f"  sent to {to}")
    else:
        rec["status"] = "DRAFTED"
    if q["decision"] == "QUALIFIED_FLAGGED":
        rec["status"] += " (flagged: thin evidence, review before a real send)"
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def write_report(run_dir: str, records: list[dict]):
    lines = ["# Sprinto outreach run\n",
             f"Model: `{config.MODEL}` | LLM calls: {llm.usage_log['calls']} | "
             f"tokens in/out: {llm.usage_log['input_tokens']}/{llm.usage_log['output_tokens']}\n",
             "| Domain | Decision | Score | PoC | Status |", "|---|---|---|---|---|"]
    for r in records:
        q = r.get("qualification", {})
        p = r.get("poc") or {}
        who = f"{p.get('first_name','')} {p.get('last_name','')} ({p.get('title','')})" if "first_name" in p else "-"
        lines.append(f"| {r['domain']} | {q.get('decision','-')} | {q.get('score','-')} | {who} | {r['status']} |")
    for r in records:
        lines.append(f"\n## {r['domain']}\n")
        q = r.get("qualification", {})
        lines.append(f"**Decision:** {q.get('decision')} - {q.get('reason')}\n")
        for s in q.get("score_reasons", []):
            lines.append(f"- {s}")
        if q.get("trace"):
            lines.append("\n**Pages fetched:** " + ", ".join(t["fetch"] + ("" if t["ok"] else f" ({t['error']})") for t in q["trace"]))
        if r.get("poc") and "why" in r["poc"]:
            lines.append(f"\n**Why this person:** {r['poc']['why']}")
        if r.get("pain_points"):
            lines.append("\n**Pain points:**")
            for pp in r["pain_points"]["pain_points"]:
                lines.append(f"- *{pp['title']}* - {pp['why_this_person_feels_it']} (evidence: {pp['evidence_from_research']})")
        if r.get("email"):
            e = r["email"]
            lines.append(f"\n**Why this email:** {e['reasoning']} Hook: {e['hook_used']}")
            lines.append(f"\n```\nSubject: {e['subject']}\n\n{e['body']}\n```")
    with open(os.path.join(run_dir, "report.md"), "w") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domains_file", nargs="?")
    ap.add_argument("--domain")
    ap.add_argument("--send", action="store_true", help="actually send (to your own inbox)")
    ap.add_argument("--sender-name", default=os.getenv("SENDER_NAME", "Growth Team, Sprinto"))
    args = ap.parse_args()

    if args.domain:
        domains = [args.domain]
    elif args.domains_file:
        with open(args.domains_file) as f:
            domains = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    else:
        ap.error("give a domains file or --domain")

    run_dir = os.path.join(os.path.dirname(__file__), "runs", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)
    records = []
    for d in domains:
        rec = process(d, args.send, args.sender_name)
        records.append(rec)
        with open(os.path.join(run_dir, d.replace("/", "_") + ".json"), "w") as f:
            json.dump(rec, f, indent=2, default=str)
    write_report(run_dir, records)
    log(f"\nDone. Report: {os.path.join(run_dir, 'report.md')}")


if __name__ == "__main__":
    main()
