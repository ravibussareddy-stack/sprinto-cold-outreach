"""Steps 5 & 6 - Pain points for this person, then a personalised email."""
import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

import config
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

EMAIL_EXAMPLES = """Example 1 (CTO, Series A fintech; hook: new enterprise plan)
Subject: SOC 2 before your first enterprise customer

Good morning Priya,

Hope you're doing well.

Congratulations on launching Ledgerly's Enterprise plan last month. The first large customer usually asks for a SOC 2 report before the second meeting.

For a team your size, that often means engineers spending weeks gathering evidence instead of shipping. Sprinto connects to your cloud and identity tools, collects that evidence automatically and gets companies audit-ready in weeks rather than months.

Would a 15-minute call next week be useful?

Kind regards,
Sarah Mitchell
Sprinto

Example 2 (Head of Security, Series B health-tech; hook: open compliance role)
Subject: Your new compliance hire

Good afternoon Tom,

Hope you're doing well.

I noticed Fernwood Health is hiring its first Compliance Manager, which usually means the security questionnaires have started to pile up.

Whoever takes the role could spend their first months in spreadsheets, chasing evidence across teams. Sprinto monitors your controls continuously and keeps audit evidence ready, so they can focus on the programme rather than the paperwork.

Might it be worth a brief conversation before they start?

Best wishes,
Sarah Mitchell
Sprinto

Example 3 (CEO, Series A logistics SaaS; hook: named customer win)
Subject: Congratulations on Brightmere

Good morning Aisha,

Hope you're doing well.

Congratulations on Northgate's partnership with Brightmere Retail. Contracts of that size tend to arrive with a lengthy security review attached.

Reviews like that can add weeks to every deal. Sprinto gets you audit-ready for SOC 2 or ISO 27001 in weeks and helps answer security questionnaires, so procurement stops being the slowest part of the sale.

Would you be open to a short call on Thursday or Friday?

Kind regards,
Sarah Mitchell
Sprinto"""

EMAIL_SYSTEM = f"""You write cold emails for Sprinto's Growth Lead that real
people answer. {SPRINTO_CONTEXT}

Style: UK business English - polite, warm, brief and to the point. UK spelling
(organisation, prioritise, programme). Catchy but never pushy.

Rules:
- Open with exactly the greeting line you are given (e.g. "Good morning Priya,"),
  then a blank line, then "Hope you're doing well." on its own line.
- The next line is the hook: a specific, true observation about THEIR company
  from the research (a funding round, an enterprise tier, a named customer, an
  open security role, an award or industry recognition). State the fact, then at
  most one general truth about what it tends to bring. Never guess about their
  team's workload ("I imagine your team...").
- Connect the hook to pain point #1 in one sentence; mention Sprinto's
  outcome concretely (e.g. audit-ready in weeks, fewer engineering hours).
- One soft, low-friction question as the CTA, UK style (e.g. "Would a
  15-minute call next week be useful?", "Might it be worth a brief conversation?").
- 75-125 words including the greeting. Plain text. No bullet lists, no emojis,
  no buzzwords ("synergy", "leverage", "revolutionise"), no fake familiarity.
- Never invent facts, numbers or customer names not in the research.
- Describe Sprinto only with what the Sprinto description above says (e.g.
  "audit-ready in weeks rather than months"). No new numbers, time savings or
  claims such as "weeks to hours".
- Sign off with "Kind regards," (or "Best wishes,") on its own line, then the
  sender name given.

The examples below show the tone, length and structure only. Never reuse their
company names, people or facts.

{EMAIL_EXAMPLES}"""

BANNED = re.compile(r"hope this (email )?finds you|revolutioni[sz]e|synergy|\[|\]|\{|\}|lorem", re.I)


def greeting(first_name: str, now: dt.datetime | None = None) -> str:
    """Time-of-day greeting in GREETING_TIMEZONE, at the moment the draft is written."""
    now = now or dt.datetime.now(ZoneInfo(config.GREETING_TIMEZONE))
    hour = now.astimezone(ZoneInfo(config.GREETING_TIMEZONE)).hour
    part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    return f"Good {part} {first_name},"


def pain_points(profile: CompanyProfile, poc: dict) -> PainPoints:
    user = (f"Contact: {poc['first_name']} {poc.get('last_name','')}, {poc.get('title')}\n\n"
            f"Company profile (JSON):\n{profile.model_dump_json(indent=1)}")
    return ask(PainPoints, PAIN_SYSTEM, user)


def validate(draft: EmailDraft, first_name: str, greeting_line: str | None = None) -> list[str]:
    problems = []
    words = len(draft.body.split())
    if first_name.lower() not in draft.body.lower()[:60]:
        problems.append("does not greet the contact by first name")
    if greeting_line and not draft.body.strip().startswith(greeting_line):
        problems.append(f'does not open with "{greeting_line}"')
    if greeting_line and "hope you're doing well" not in draft.body.lower().replace("’", "'"):
        problems.append('missing "Hope you\'re doing well." line')
    if not 60 <= words <= 140:
        problems.append(f"length {words} words (want 75-125)")
    if BANNED.search(draft.body + draft.subject):
        problems.append("contains banned phrase or placeholder bracket")
    if draft.body.count("?") < 1:
        problems.append("no question CTA")
    return problems


def draft_email(profile: CompanyProfile, poc: dict, pains: PainPoints, sender_name: str,
                max_attempts: int = 2) -> tuple[EmailDraft, list[str]]:
    hello = greeting(poc["first_name"])
    base = (f"Recipient: {poc['first_name']} {poc.get('last_name','')}, {poc.get('title')} at "
            f"{profile.company_name}\nSender name: {sender_name}\n"
            f"Greeting line to open with: {hello}\n\n"
            f"Pain points (use #1 as the main angle):\n{json.dumps(pains.model_dump(), indent=1)}\n\n"
            f"Research:\n{profile.model_dump_json(indent=1)}")
    feedback = ""
    for _ in range(max_attempts):
        draft = ask(EmailDraft, EMAIL_SYSTEM, base + feedback)
        problems = validate(draft, poc["first_name"], hello)
        if not problems:
            return draft, []
        feedback = f"\n\nYour previous draft failed these checks, fix them: {problems}\nPrevious draft:\n{draft.body}"
    return draft, problems
