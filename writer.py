"""Steps 5 & 6 - Pain points for this person, then a personalised email."""
import json
import re

from llm import ask
from schemas import CompanyProfile, EmailDraft, PainPoints

SPRINTO_CONTEXT = """Sprinto (sprinto.com) automates compliance for SaaS companies:
SOC 2, ISO 27001, GDPR, HIPAA and more. It connects to cloud, HR, code and
identity tools, continuously monitors controls, collects audit evidence
automatically, ships ready policies and security training, and gets companies
audit-ready in weeks instead of months - with far less engineering time.
It also helps answer security questionnaires and keeps compliance "always on"
after the first audit."""

PAIN_SYSTEM = f"""You are a sharp B2B sales researcher for Sprinto.
{SPRINTO_CONTEXT}

Given a researched company profile and the contact's role, name the TWO most
likely pain points this specific person feels about security compliance right
now. Each must be grounded in a concrete fact from the profile (quote it in
evidence_from_research). Prefer role-specific pains (a CTO loses engineering
weeks to evidence collection; a CEO loses deals stuck in security review; a
security lead drowns in questionnaires and spreadsheets). No generic pains."""

EMAIL_SYSTEM = f"""You write cold emails for Sprinto's Growth Lead that real
people answer. {SPRINTO_CONTEXT}

Rules:
- Greet the recipient by first name.
- Line 1 is the hook: a specific, true observation about THEIR company from
  the research (a funding round, an enterprise tier, a named customer, an
  open security role, a 'SOC 2 in progress' note). Never "I hope this finds you well".
- Connect the hook to pain point #1 in one sentence; mention Sprinto's
  outcome concretely (e.g. audit-ready in weeks, fewer engineering hours).
- One low-friction question as the CTA (e.g. "Worth a 15-min look?").
- 70-120 words. Plain text. No bullet lists, no emojis, no buzzwords
  ("synergy", "leverage", "revolutionize"), no fake familiarity.
- Never invent facts, numbers or customer names not in the research.
- Sign off with the sender name given."""

BANNED = re.compile(r"hope this (email )?finds you|revolutioni[sz]e|synergy|\[|\]|\{|\}|lorem", re.I)


def pain_points(profile: CompanyProfile, poc: dict) -> PainPoints:
    user = (f"Contact: {poc['first_name']} {poc.get('last_name','')}, {poc.get('title')}\n\n"
            f"Company profile (JSON):\n{profile.model_dump_json(indent=1)}")
    return ask(PainPoints, PAIN_SYSTEM, user)


def validate(draft: EmailDraft, first_name: str) -> list[str]:
    problems = []
    words = len(draft.body.split())
    if first_name.lower() not in draft.body.lower()[:60]:
        problems.append("does not greet the contact by first name")
    if not 60 <= words <= 140:
        problems.append(f"length {words} words (want 70-120)")
    if BANNED.search(draft.body + draft.subject):
        problems.append("contains banned phrase or placeholder bracket")
    if draft.body.count("?") < 1:
        problems.append("no question CTA")
    return problems


def draft_email(profile: CompanyProfile, poc: dict, pains: PainPoints, sender_name: str,
                max_attempts: int = 2) -> tuple[EmailDraft, list[str]]:
    base = (f"Recipient: {poc['first_name']} {poc.get('last_name','')}, {poc.get('title')} at "
            f"{profile.company_name}\nSender name: {sender_name}\n\n"
            f"Pain points (use #1 as the main angle):\n{json.dumps(pains.model_dump(), indent=1)}\n\n"
            f"Research:\n{profile.model_dump_json(indent=1)}")
    feedback = ""
    for _ in range(max_attempts):
        draft = ask(EmailDraft, EMAIL_SYSTEM, base + feedback)
        problems = validate(draft, poc["first_name"])
        if not problems:
            return draft, []
        feedback = f"\n\nYour previous draft failed these checks, fix them: {problems}\nPrevious draft:\n{draft.body}"
    return draft, problems
