"""Redraft one email with the latest feedback, and measure whether it got better.

Takes a saved run (its research, contact and pain points), drafts the email again with the
current feedback repo and winning examples, re-runs the quality checks and the disagreement
score, and saves the result as a new run linked to the original. Nothing is sent.
The original leaves the review queue (recorded as "redrafted").

    python redraft.py 28          # or POST /api/redraft {"run_id": 28}
"""
import os
import re
import sys
import time

import db
import disagreement
import feedback
import llm
import rubric
import writer
from schemas import CompanyProfile, PainPoints


class RedraftError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _summary(run_id, rec: dict) -> dict:
    d = rec.get("disagreement") or {}
    e = rec.get("email") or {}
    q = rec.get("quality") or {}
    return {"run_id": run_id, "subject": e.get("subject"), "body": e.get("body"),
            "disagreement": d.get("score"), "parts": d.get("parts"),
            "wrong_info": d.get("claims_unsupported", []),
            "quality": None if not q else f"{q.get('passed')}/{q.get('total')}"}


def sender_of(rec: dict) -> str | None:
    """The sender the original email was signed by: saved on newer runs, else read from the
    lines after the sign-off ("Kind regards,\nRavi Bussareddy\nSprinto" -> "Ravi Bussareddy, Sprinto")."""
    if rec.get("sender_name"):
        return rec["sender_name"]
    lines = [l.strip() for l in ((rec.get("email") or {}).get("body") or "").strip().splitlines()]
    for i, l in enumerate(lines):
        if writer.SIGN_OFF_RE.match(l) and len(l) < 25:
            rest = [x for x in lines[i + 1:] if x]
            return ", ".join(rest) if rest else None
    return None


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_contact(raw) -> dict:
    """A recipient typed on the draft page. First name needed; email optional but valid if given."""
    if not isinstance(raw, dict):
        raise RedraftError("invalid_contact", "contact must be an object")
    c = {}
    for k in ("first_name", "last_name", "email", "title"):
        v = raw.get(k)
        if v is not None and not isinstance(v, str):
            raise RedraftError("invalid_contact", f"contact.{k} must be text")
        c[k] = (v or "").strip()[:120]
    if not c["first_name"]:
        raise RedraftError("invalid_contact", "the contact needs a first name")
    if c["email"] and not EMAIL_RE.match(c["email"]):
        raise RedraftError("invalid_contact", "that email is not valid")
    c["title"] = c["title"] or None
    return c


def redraft(run_id: int, sender_name: str | None = None, contact: dict | None = None) -> dict:
    """Low thinking level and the two scoring calls in parallel: about 15-25 seconds.
    contact: write it for a different person (greeting, and pain points if the role changed)."""
    t0 = time.time()
    run = db.get_run(run_id)
    if not run:
        raise RedraftError("run_not_found", f"no run with id {run_id}", 404)
    rec = run["record"] or {}
    if not rec.get("email") or not rec.get("profile") or not rec.get("pain_points"):
        raise RedraftError("not_a_draft", "this run has no drafted email to redo")
    poc = rec.get("poc") or {}
    new_person = False
    if contact is not None:
        c = clean_contact(contact)
        new_person = (c["title"] or "") != (poc.get("title") or "")
        poc = c | {"source": "changed by you on the draft page"}
    if "first_name" not in poc:
        raise RedraftError("no_contact", "this run has no contact to write to; add one on the draft page")
    llm.context["domain"] = run["domain"]
    if db.enabled():
        llm.on_usage = db.record_usage
    profile = CompanyProfile(**rec["profile"])
    pains = PainPoints(**rec["pain_points"])
    sender_name = sender_name or sender_of(rec) or os.getenv("SENDER_NAME", "Growth Team, Sprinto")
    guidance = feedback.relevant(poc.get("title"), profile.target_industries)
    examples = feedback.exemplars(poc.get("title"))

    llm.context["fast"] = True                  # low thinking level: about 3x quicker per call
    try:
        if new_person:                          # a different role feels different pains
            pains = writer.pain_points(profile, poc)
        draft, problems = writer.draft_email(profile, poc, pains, sender_name, guidance=guidance, exemplars=examples)
        quality = writer.quality_summary(writer.quality_checks(
            draft, poc["first_name"], writer.greeting(poc["first_name"]), sender_name, profile.company_name))
        structure = quality["failed"] + [p for p in problems if p not in quality["failed"]]
        dis = disagreement.assess(draft.body, profile.model_dump_json(), structure)
    finally:
        llm.context["fast"] = False
    threshold = rubric.get("review", "disagreement_threshold")

    new = {k: rec.get(k) for k in ("domain", "qualification", "profile", "pages_read")}
    new.update(poc=poc, pain_points=pains.model_dump())
    new.update(status="REDRAFTED", source="redraft", parent_run_id=run_id, email=draft.model_dump(),
               sender_name=sender_name, seconds=round(time.time() - t0, 1),
               email_check_failures=problems, quality=quality, disagreement=dis,
               needs_review=dis["score"] > threshold,
               guidance_used=[{"id": g["id"], "scope": g["scope"], "score": g["outcome_score"]} for g in guidance])
    new_id = db.save_run(new)
    db.add_run_review(run_id, run["domain"], "redrafted", f"redrafted with feedback as run {new_id}")
    before, after = _summary(run_id, rec), _summary(new_id, new)
    delta = None if before["disagreement"] is None else after["disagreement"] - before["disagreement"]
    return {"before": before, "after": after, "change": delta, "improved": delta is not None and delta < 0,
            "guidance_used": [{"id": g["id"], "scope": g["scope"], "score": g["outcome_score"], "guidance": g["guidance"]}
                              for g in guidance],
            "examples_used": len(examples), "needs_review": new["needs_review"], "threshold": threshold,
            "seconds": new["seconds"]}


def send_run(run_id: int) -> dict:
    """The draft page's Send button: send this draft (to our own inbox, with the deck) and start its
    conversation - the same step the pipeline uses (pipeline.deliver)."""
    import budget
    import pipeline
    run = db.get_run(run_id)
    if not run:
        raise RedraftError("run_not_found", f"no run with id {run_id}", 404)
    rec = run["record"] or {}
    if (rec.get("status") or "").startswith("SENT"):
        raise RedraftError("already_sent", "this draft has already been sent", 409)
    if not rec.get("email") or not rec.get("profile") or not rec.get("pain_points"):
        raise RedraftError("not_a_draft", "this run has no drafted email to send")
    poc = rec.get("poc") or {}
    if "first_name" not in poc:
        raise RedraftError("no_contact", "add a recipient first (first name at least), then redraft for them")
    if rec.get("email_check_failures"):
        raise RedraftError("failed_checks", "this draft failed a required check ("
                           + ", ".join(rec["email_check_failures"]) + "); redraft it first")
    b = budget.status()
    if b["paused"]:
        raise RedraftError("budget_exceeded", budget.pause_message(b), 409)
    llm.context["domain"] = run["domain"]
    if db.enabled():
        llm.on_usage = db.record_usage
    from schemas import EmailDraft
    draft = EmailDraft(**rec["email"])
    sender_name = sender_of(rec) or os.getenv("SENDER_NAME", "Growth Team, Sprinto")
    out = pipeline.deliver(run["domain"], poc, CompanyProfile(**rec["profile"]), PainPoints(**rec["pain_points"]),
                           draft, sender_name, rec.get("quality"), log=lambda m: None)
    rec.update(out)
    rec["sent_from"] = "dashboard"
    db.update_run(run_id, rec["status"], rec)
    return {"ok": True, "run_id": run_id, "status": rec["status"], "sent_to": out.get("sent_to"),
            "deck_attached": out.get("deck_attached"), "thread_id": out.get("thread_id")}


if __name__ == "__main__":
    import json
    print(json.dumps(redraft(int(sys.argv[1])), indent=1, default=str))
