"""Redraft one email with the latest feedback, and measure whether it got better.

Takes a saved run (its research, contact and pain points), drafts the email again with the
current feedback repo and winning examples, re-runs the quality checks and the disagreement
score, and saves the result as a new run linked to the original. Nothing is sent.
The original leaves the review queue (recorded as "redrafted").

    python redraft.py 28          # or POST /api/redraft {"run_id": 28}
"""
import os
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


def redraft(run_id: int, sender_name: str | None = None) -> dict:
    """Low thinking level and the two scoring calls in parallel: about 15-25 seconds."""
    t0 = time.time()
    run = db.get_run(run_id)
    if not run:
        raise RedraftError("run_not_found", f"no run with id {run_id}", 404)
    rec = run["record"] or {}
    if not rec.get("email") or not rec.get("profile") or not rec.get("pain_points"):
        raise RedraftError("not_a_draft", "this run has no drafted email to redo")
    poc = rec.get("poc") or {}
    if "first_name" not in poc:
        raise RedraftError("no_contact", "this run has no contact to write to")
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
        draft, problems = writer.draft_email(profile, poc, pains, sender_name, guidance=guidance, exemplars=examples)
        quality = writer.quality_summary(writer.quality_checks(
            draft, poc["first_name"], writer.greeting(poc["first_name"]), sender_name, profile.company_name))
        structure = quality["failed"] + [p for p in problems if p not in quality["failed"]]
        dis = disagreement.assess(draft.body, profile.model_dump_json(), structure)
    finally:
        llm.context["fast"] = False
    threshold = rubric.get("review", "disagreement_threshold")

    new = {k: rec.get(k) for k in ("domain", "qualification", "profile", "pages_read", "poc", "pain_points")}
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


if __name__ == "__main__":
    import json
    print(json.dumps(redraft(int(sys.argv[1])), indent=1, default=str))
