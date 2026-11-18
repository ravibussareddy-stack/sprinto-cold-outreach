"""Sprinto cold outreach - end-to-end pipeline.

    python pipeline.py domains.txt              # research + draft, no send (dry run)
    python pipeline.py domains.txt --send       # also send to YOUR inbox
    python pipeline.py --domain acme.io --send  # single company

For every domain: scrape -> extract -> qualify (agent loop) -> find PoC ->
pain points -> draft email -> send. Failures are logged and the run moves on.
Every decision is written to runs/<timestamp>/ and, if NEON_DATABASE_URL is set,
to the Neon `runs` table so it can be defended later.
"""
import argparse
import datetime as dt
import json
import os
import time
from dataclasses import asdict

import budget
import config
import db
import llm
import disagreement
import feedback
import meeting
import poc as poc_mod
import replies
import rubric
import sender
import writer
from qualify import qualify
from pipeline_rules import reactivation_new_certs


def log(msg):
    print(msg, flush=True)


def process(domain: str, do_send: bool, sender_name: str, contact: dict | None = None) -> dict:
    """Run one company. Any unexpected error is recorded on the company and the
    batch carries on (degrade gracefully: log the reason, move on)."""
    t0 = time.time()
    rec = {"domain": domain, "status": None}
    log(f"\n=== {domain} ===")
    b = budget.status()
    if b["paused"]:
        log("  PAUSED: " + budget.pause_message(b))
        return rec | {"status": "PAUSED", "error": budget.pause_message(b), "budget": b, "seconds": 0.0}
    try:
        return _process(domain, do_send, sender_name, rec, t0, contact)
    except Exception as e:
        log(f"  ERROR: {e}")
        rec.update(status="ERROR", error=f"{type(e).__name__}: {e}",
                   seconds=round(time.time() - t0, 1))
        return rec


def _process(domain, do_send, sender_name, rec, t0, contact=None):
    llm.context["domain"] = domain               # token use is recorded per company
    tokens_before = (llm.usage_log["input_tokens"], llm.usage_log["output_tokens"])
    try:
        return _process_inner(domain, do_send, sender_name, rec, t0, contact)
    finally:
        rec["usage"] = {"input_tokens": llm.usage_log["input_tokens"] - tokens_before[0],
                        "output_tokens": llm.usage_log["output_tokens"] - tokens_before[1]}


def _process_inner(domain, do_send, sender_name, rec, t0, given_contact=None):

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
    if q["decision"] == "DORMANT":
        new_certs = reactivation_new_certs(db.last_run(domain), q.get("certs_held", []))
        if new_certs:
            q["decision"] = "REACTIVATED"
            q["reason"] = f"new certificate since last run: {', '.join(new_certs)}. Review before outreach."
            q["flags"] = q.get("flags", []) + [q["reason"]]
    if q["decision"] not in ("QUALIFIED", "QUALIFIED_FLAGGED", "REACTIVATED"):
        rec["status"] = q["decision"]
        return rec

    # Step 4: PoC
    profile = q["profile"]
    # A contact supplied with the case (API / test input) wins over the lookup.
    contact = dict(given_contact, source="provided with the case") if given_contact else \
        poc_mod.find_poc(domain, profile.employee_estimate)
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

    # Step 6: email - with the ranked lessons and winning emails from the feedback repo
    guidance = feedback.relevant(contact.get("title"), profile.target_industries)
    examples = feedback.exemplars(contact.get("title"))
    if guidance or examples:
        log(f"  using {len(guidance)} lesson(s) and {len(examples)} winning email(s) from the feedback repo")
    draft, problems = writer.draft_email(profile, contact, pains, sender_name,
                                         guidance=guidance, exemplars=examples)
    rec["email"] = draft.model_dump()
    rec["email_check_failures"] = problems
    rec["guidance_used"] = [{"id": g["id"], "scope": g["scope"], "score": g["outcome_score"]} for g in guidance]
    log(f"  subject: {draft.subject}\n" + "\n".join("    " + l for l in draft.body.splitlines()))

    # Quality checks: the structure of the email, check by check
    quality = writer.quality_summary(writer.quality_checks(
        draft, contact["first_name"], writer.greeting(contact["first_name"]), sender_name, profile.company_name))
    rec["quality"] = quality
    log(f"  quality: {quality['passed']}/{quality['total']} checks passed"
        + (f" (failed: {', '.join(quality['failed'])})" if quality["failed"] else ""))

    # Disagreement score (build 1: reported only; build 2 holds drafts above a threshold)
    try:
        structure_failures = quality["failed"] + [p for p in problems if p not in quality["failed"]]
        rec["disagreement"] = disagreement.assess(draft.body, profile.model_dump_json(), structure_failures)
        log(f"  disagreement: {rec['disagreement']['score']}/100 {rec['disagreement']['parts']}")
        threshold = rubric.get("review", "disagreement_threshold")
        rec["needs_review"] = rec["disagreement"]["score"] > threshold
        if rec["needs_review"]:
            log(f"  -> above {threshold}: added to the review queue on the dashboard")
    except Exception as e:
        rec["disagreement"] = {"error": f"{type(e).__name__}: {e}"}
        log(f"  disagreement: not computed ({e})")

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
    # Sequence 1 reply handling, then Sequence 2 scheduling if the prospect is interested.
    # Only after a draft that passed its checks; dry runs walk the flow without sending.
    if not problems:
        research = {"company": profile.company_name, "what_they_do": profile.one_line_description,
                    "named_customers": profile.named_customers[:5], "recent_triggers": profile.recent_triggers[:3],
                    "industries": profile.target_industries[:3],
                    "pain_points": [p.title for p in pains.pain_points]}
        ctx = meeting.Ctx(domain=domain, contact_name=f"{contact['first_name']} {contact['last_name']}",
                          contact_email=contact["email"], company=profile.company_name or domain,
                          sender_name=sender_name, do_send=do_send, log=log, subject=draft.subject,
                          title=contact.get("title"), industries=profile.target_industries[:3],
                          research=research)
        if config.REPLY_MODE == "gmail" and do_send and db.enabled():
            # Real replies: save the conversation and let check_replies.py continue it.
            thread_id, state, _ = meeting.start(ctx)
            db.save_thread(thread_id, domain, draft.subject,
                           {"domain": domain, "contact_name": ctx.contact_name,
                            "contact_email": ctx.contact_email, "company": ctx.company,
                            "sender_name": sender_name, "title": ctx.title,
                            "industries": ctx.industries, "research": research})
            db.log_email(thread_id, domain, "cold", draft.subject, draft.body, quality)
            db.upsert_contact(domain, ctx.company, ctx.contact_name, ctx.contact_email, ctx.title, "contacted")
            rec["thread_id"] = thread_id
            rec["meeting"] = asdict(state)
            rec["outcome"] = "awaiting_reply"
            rec["status"] += " | awaiting_reply"
            log(f"  waiting for a reply in Gmail (thread {thread_id})")
        else:
            if do_send:                          # log the cold email here too (Gmail mode logs it with its thread)
                db.log_email(None, domain, "cold", draft.subject, draft.body, quality)
            state = meeting.run(ctx, replies.load_replies(domain))
            rec["meeting"] = asdict(state)
            rec["outcome"] = state.outcome
            rec["status"] += f" | {state.outcome}"
    if q["decision"] == "QUALIFIED_FLAGGED":
        rec["status"] += " (flagged: thin evidence, review before a real send)"
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def write_report(run_dir: str, records: list[dict]):
    lines = ["# Sprinto outreach run\n",
             f"Model: `{config.GEMINI_MODEL}` | LLM calls: {llm.usage_log['calls']} | "
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
        for fl in q.get("flags", []):
            lines.append(f"- FLAG: {fl}")
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
        if r.get("meeting"):
            m = r["meeting"]
            lines.append(f"\n**Scheduling outcome:** {m['outcome']}"
                         + (f" - escalated: {m['escalation']}" if m.get("escalation") else "")
                         + (f" - booked: {m['booked_slot']}" if m.get("booked_slot") else ""))
            lines.append("**Steps:** " + "; ".join(m.get("trace", [])))
        if r.get("disagreement") and "score" in r["disagreement"]:
            d = r["disagreement"]
            lines.append(f"\n**Disagreement score:** {d['score']}/100 (rules {d['parts']['rules']}, "
                         f"claims {d['parts']['claims']}, judge {d['parts']['judge']})")
            for c in d["claims_unsupported"]:
                lines.append(f"- unsupported claim: {c}")
            if d["judge_reason"] and not d["judge_faithful"]:
                lines.append(f"- judge: {d['judge_reason']}")
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
    if db.enabled():
        llm.on_usage = db.record_usage           # token use per call -> llm_usage table

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
        db.save_run(rec)
        with open(os.path.join(run_dir, d.replace("/", "_") + ".json"), "w") as f:
            json.dump(rec, f, indent=2, default=str)
    write_report(run_dir, records)
    log(f"\nDone. Report: {os.path.join(run_dir, 'report.md')}")


if __name__ == "__main__":
    main()
