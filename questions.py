"""Questions the system asks you when it is unsure, and how your answers are used.

Each question has a key (one per company and topic), a kind, and either options or a free-form
answer. Answers are read on the company's next run (or, for conversations, applied by the dashboard
straight away). Nothing here blocks a run: an open question just waits on the Questions page.

    saas:<domain>          Is it a B2B SaaS company?           -> yes | no
    certs:<domain>         Does it hold SOC 2 / ISO 27001?      -> soc2 | iso | both | none | unsure
    contact:<domain>       Who should we email?                 -> {first_name, last_name, email, title}
    conversation:<thread>  How should we handle this reply?     -> self | close (applied by the dashboard)
"""
import db

ANSWER_PAGE = "answer://you/security"      # how a certification answer enters the evidence (a security page)

CERT_OPTIONS = [{"value": "soc2", "label": "Yes, SOC 2"}, {"value": "iso", "label": "Yes, ISO 27001"},
                {"value": "both", "label": "Yes, both"}, {"value": "none", "label": "Neither"},
                {"value": "unsure", "label": "Not sure"}]
SAAS_OPTIONS = [{"value": "yes", "label": "Yes, B2B SaaS"}, {"value": "no", "label": "No, skip it"}]
CONVERSATION_OPTIONS = [{"value": "self", "label": "I'll reply myself"}, {"value": "close", "label": "Close it as a cold lead"}]


def answer(key: str):
    return db.answer_for(key) if db.enabled() else None


def cert_answer_page(domain: str) -> dict | None:
    """Your certification answer as a page of evidence, so it flows through the normal rules
    (and shows in the evidence as 'confirmed by you')."""
    a = answer(f"certs:{domain}")
    if a not in ("soc2", "iso", "both", "none"):
        return None
    text = {"soc2": "The company is SOC 2 certified (confirmed by the user).",
            "iso": "The company is ISO 27001 certified (confirmed by the user).",
            "both": "The company is SOC 2 certified and ISO 27001 certified (confirmed by the user).",
            "none": "Confirmed by the user: the company holds neither SOC 2 nor ISO 27001."}[a]
    import scraper
    return {"url": ANSWER_PAGE, "ok": True, "error": None, "method": "your answer", "text": text,
            "raw_text": text, "certs": scraper.detect_certifications(text), "links": []}


def contact_answer(domain: str) -> dict | None:
    a = answer(f"contact:{domain}")
    if isinstance(a, dict) and a.get("first_name") and a.get("email"):
        return {"first_name": a["first_name"], "last_name": a.get("last_name") or "", "email": a["email"],
                "title": a.get("title") or None, "source": "given by you (Questions)"}
    return None


def mark_used(domain: str, decision: str | None):
    """Record on each answered question about this company that its answer was used, and the result."""
    if not db.enabled():
        return
    import datetime as dt
    for k in (f"saas:{domain}", f"certs:{domain}", f"contact:{domain}"):
        if answer(k) is not None:
            db.mark_applied(k, f"Used on the run of {dt.date.today():%d %b %Y}: {decision or 'processed'}.")


def ask_after_qualify(domain: str, company: str, q: dict, profile, run_id=None):
    """After qualification: ask what would change the decision and could not be found."""
    if not db.enabled():
        return
    cert = (q.get("certification") or {}).get("status")
    if q.get("decision") == "MANUAL_REVIEW" and profile is not None and profile.is_b2b_saas is None:
        db.ask_question(f"saas:{domain}", "saas", "qualification", domain,
                        f"Is {company} a B2B SaaS company?",
                        "Its website did not make clear whether it sells software to businesses, so it was not scored. "
                        "Your answer is used the next time it runs.", SAAS_OPTIONS, run_id)
    if cert == "unknown" and q.get("decision") in ("QUALIFIED", "QUALIFIED_FLAGGED", "MANUAL_REVIEW"):
        db.ask_question(f"certs:{domain}", "certs", "qualification", domain,
                        f"Does {company} already hold SOC 2 or ISO 27001?",
                        "No security or trust page was found, so its certification status is unknown and the "
                        "greenfield points were held back. If it holds either, it is dormant (no email); if neither, "
                        "it earns the greenfield points. Used the next time it runs.", CERT_OPTIONS, run_id)


def ask_for_contact(domain: str, company: str, reason: str, run_id=None):
    if db.enabled():
        db.ask_question(f"contact:{domain}", "contact", "contact lookup", domain,
                        f"Who should we email at {company}?",
                        f"No suitable decision-maker was found ({reason}). Give a name and email and it is used "
                        "the next time it runs. Emails still go only to our own inbox.", None, run_id)


def ask_about_conversation(thread_id: str, domain: str, company: str, why: str):
    if db.enabled():
        db.ask_question(f"conversation:{thread_id}", "conversation", "reply handling", domain,
                        f"How should we handle the conversation with {company}?",
                        f"The reply could not be handled automatically: {why}.", CONVERSATION_OPTIONS)
