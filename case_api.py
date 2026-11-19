"""Run test cases through the full pipeline over HTTP (POST /api/run_case on Vercel).

Input (JSON). One case:
    {
      "domain": "atlan.com",                       # required; a URL also works (https://www.atlan.com/x)
      "contact": {                                 # optional: the PoC to write to (Option B / Apollo)
        "first_name": "Priya", "last_name": "Sharma",   # or "name": "Priya Sharma"
        "email": "priya@atlan.com", "title": "CTO"
      },
      "send": true                                 # optional, default true: email the draft to SEND_TO
    }
Every case runs the full pipeline (about 60-80 seconds), disagreement score included. A "mode" field
from older clients is accepted and ignored.
Several cases: {"cases": [ {...}, {...} ]}  (up to MAX_CASES per request).
Aliases accepted: "website" / "url" / "company_domain" for domain; "poc" for contact.

Output: one result per case (see result_for). HTTP status:
    200 every case was processed (a company can still be DISQUALIFIED, DORMANT, NO_POC or ERROR -
        those are results, with a reason, not crashes)
    400 the request itself is invalid (bad JSON, missing or malformed domain, bad contact) - nothing ran
    405 wrong method
Every error has {"error": {"code": ..., "message": ...}} and the service keeps running.
"""
import re
import time

import config
import db
import llm

MAX_CASES = 3
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

INPUT_FORMAT = {
    "method": "POST",
    "path": "/api/run_case",
    "body": {
        "domain": "atlan.com  (required; a URL also works)",
        "contact": {"first_name": "Priya", "last_name": "Sharma", "email": "priya@atlan.com",
                    "title": "CTO", "note": "optional; 'name' may replace first/last"},
        "send": "true | false (optional, default true: emails the draft to the configured inbox)",
    },
    "many_cases": {"cases": ["<case>", "<case>"], "max": MAX_CASES},
}


def _err(code: str, message: str, field: str | None = None) -> dict:
    e = {"code": code, "message": message}
    if field:
        e["field"] = field
    return e


def normalise_domain(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    d = raw.strip().lower()
    d = re.sub(r"^[a-z]+://", "", d)          # scheme
    d = d.split("/")[0].split("?")[0].split("#")[0].split(":")[0]
    d = d[4:] if d.startswith("www.") else d
    return d if DOMAIN_RE.match(d) else None


def parse_contact(raw) -> tuple[dict | None, dict | None]:
    """Returns (contact, error). No contact given is fine (None, None)."""
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, _err("invalid_contact", "contact must be an object", "contact")
    for key in ("first_name", "last_name", "name", "email", "title"):     # type-check before any .strip()
        if raw.get(key) is not None and not isinstance(raw[key], str):
            return None, _err("invalid_contact", f"contact.{key} must be a string, got {type(raw[key]).__name__}",
                              f"contact.{key}")
    first, last = (raw.get("first_name") or "").strip(), (raw.get("last_name") or "").strip()
    if not first and isinstance(raw.get("name"), str) and raw["name"].strip():
        parts = raw["name"].strip().split()
        first, last = parts[0], " ".join(parts[1:])
    email = (raw.get("email") or "").strip()
    if not first:
        return None, _err("invalid_contact", "contact needs a first_name (or name)", "contact.first_name")
    if email and not EMAIL_RE.match(email):              # optional (every send goes to our own inbox), but valid if given
        return None, _err("invalid_contact", "contact.email is not a valid email", "contact.email")
    return {"first_name": first, "last_name": last, "email": email,
            "title": (raw.get("title") or "").strip() or None}, None


def parse_case(raw) -> tuple[dict | None, dict | None]:
    if not isinstance(raw, dict):
        return None, _err("invalid_case", "each case must be a JSON object")
    given = next((raw[k] for k in ("domain", "website", "url", "company_domain") if raw.get(k) is not None), None)
    if given is None:
        return None, _err("missing_domain", "domain is required", "domain")
    domain = normalise_domain(given)
    if not domain:
        return None, _err("invalid_domain", f"not a valid domain: {str(given)[:100]!r}", "domain")
    contact, cerr = parse_contact(raw.get("contact", raw.get("poc")))
    if cerr:
        return None, cerr
    send = raw.get("send", True)
    if not isinstance(send, bool):
        return None, _err("invalid_send", "send must be true or false", "send")
    manual = raw.get("manual", False)
    if not isinstance(manual, bool):
        return None, _err("invalid_body", "manual must be true or false", "manual")
    return {"domain": domain, "contact": contact, "send": send, "manual": manual}, None


def parse_request(body) -> tuple[list[dict] | None, dict | None]:
    if not isinstance(body, dict):
        return None, _err("invalid_body", "the body must be a JSON object")
    raw_cases = body["cases"] if "cases" in body else [body]
    if not isinstance(raw_cases, list) or not raw_cases:
        return None, _err("invalid_cases", "cases must be a non-empty list", "cases")
    if len(raw_cases) > MAX_CASES:
        return None, _err("too_many_cases", f"at most {MAX_CASES} cases per request", "cases")
    cases = []
    for i, raw in enumerate(raw_cases):
        case, err = parse_case(raw)
        if err:
            if len(raw_cases) > 1:
                err["case_index"] = i
            return None, err
        cases.append(case)
    return cases, None


ERROR_CODES = {"ERROR": "pipeline_error", "FAILED": "site_unreachable", "NO_POC": "no_contact_found",
               "PAUSED": "budget_exceeded",
               "DRAFT_FAILED_CHECKS": "draft_failed_checks"}


def result_for(rec: dict, case: dict, run_id, warnings: list[str]) -> dict:
    q = rec.get("qualification") or {}
    status = (rec.get("status") or "ERROR").split(" |")[0].split(" (")[0]
    email = rec.get("email")
    poc = rec.get("poc") or {}
    d = rec.get("disagreement") or {}
    out = {
        "domain": case["domain"],
        "status": status,
        "decision": q.get("decision"),
        "qualified": q.get("decision") in ("QUALIFIED", "QUALIFIED_FLAGGED", "REACTIVATED"),
        "company": {
            "name": (rec.get("profile") or {}).get("company_name"),
            "fit_score": q.get("score"),
            "reason": q.get("reason"),
            "score_reasons": q.get("score_reasons", []),
            "flags": q.get("flags", []),
            "pages_read": rec.get("pages_read", []),
            "certification": q.get("certification"),
            "firmographics": (q.get("firmographics") or {}) | ({"employees_hunter": rec["company_size_hunter"]}
                                                                if (rec.get("company_size_hunter") or {}).get("headcount") else {}),
        },
        "contact": None if "first_name" not in poc else {
            "first_name": poc.get("first_name"), "last_name": poc.get("last_name"), "email": poc.get("email"),
            "title": poc.get("title"), "source": poc.get("source")},
        "pain_points": [{"title": p.get("title"), "why": p.get("why_this_person_feels_it"),
                         "evidence": p.get("evidence_from_research"), "source_url": p.get("source_url"),
                         "sprinto_angle": p.get("sprinto_angle")}
                        for p in (rec.get("pain_points") or {}).get("pain_points", [])],
        "email": None if not email else {
            "subject": email.get("subject"), "body": email.get("body"),
            "addressed_to": poc.get("first_name"), "hook": email.get("hook_used"),
            "why_this_angle": email.get("reasoning"),
            "sent": status == "SENT", "sent_to": config.SEND_TO if status == "SENT" else None,
            "intended_recipient": poc.get("email")},
        "quality": None if not rec.get("quality") else
            {k: rec["quality"][k] for k in ("passed", "total", "failed")} |
            {"label": f"{rec['quality']['passed']}/{rec['quality']['total']} draft checks",
             "note": "structure and style of the email only; facts are checked by the disagreement score"},
        "disagreement": None if "score" not in d else {"score": d["score"], "parts": d.get("parts"),
                                                        "needs_review": bool(rec.get("needs_review"))},
        "run_id": run_id,
        "seconds": rec.get("seconds"),
        "warnings": warnings,
        "error": None,
    }
    if status in ERROR_CODES or rec.get("error"):
        out["error"] = _err(ERROR_CODES.get(status, "pipeline_error"),
                            rec.get("error") or (poc.get("error") if status == "NO_POC" else None)
                            or (q.get("reason") if status == "FAILED" else None)
                            or ", ".join(rec.get("email_check_failures") or []) or status)
    return out


def run_cases(body) -> tuple[int, dict]:
    """The whole request: (http_status, response_json). Never raises."""
    import pipeline                               # heavy import, only when a case actually runs
    cases, err = parse_request(body)
    if err:
        return 400, {"error": err, "input_format": INPUT_FORMAT}
    if db.enabled():
        llm.on_usage = db.record_usage
    results = []
    gmail_ready = bool(config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD and config.SEND_TO)
    for case in cases:
        warnings = []
        do_send = case["send"] and gmail_ready
        if case["send"] and not gmail_ready:
            warnings.append("send requested but Gmail is not configured on this server; the draft was not sent")
        t0 = time.time()
        try:
            rec = pipeline.process(case["domain"], do_send, config_sender_name(), contact=case["contact"])
        except Exception as e:                    # process() already catches; this is a last line of defence
            rec = {"domain": case["domain"], "status": "ERROR", "error": f"{type(e).__name__}: {e}"}
        rec.setdefault("seconds", round(time.time() - t0, 1))
        rec["source"] = "manual contact" if case.get("manual") else "api"
        run_id = db.save_run(rec)
        if case.get("manual") and case["contact"] and not (rec.get("status") or "").startswith("SENT"):
            # A contact you added is listed even when no email went out, with the reason.
            c = case["contact"]
            company = (rec.get("profile") or {}).get("company_name") or case["domain"]
            db.upsert_contact(case["domain"], company, f"{c['first_name']} {c.get('last_name') or ''}".strip(),
                              c.get("email") or "", c.get("title"), "not_contacted",
                              (rec.get("qualification") or {}).get("decision") or (rec.get("status") or "").split(" ")[0])
        results.append(result_for(rec, case, run_id, warnings))
    body_out = results[0] if len(results) == 1 and "cases" not in body else {"results": results}
    return 200, body_out


def config_sender_name() -> str:
    import os
    return os.getenv("SENDER_NAME", "Growth Team, Sprinto")
