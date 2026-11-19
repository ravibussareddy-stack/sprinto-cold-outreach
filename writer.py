"""Steps 5 & 6 - Pain points for this person, then a personalised email."""
import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

import config
from llm import ask
from pydantic import BaseModel, Field

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


US_SPELLINGS = re.compile(
    r"\b(\w*(?:organiz|prioritiz|optimiz|analyz|realiz|recogniz|minimiz|maximiz|standardiz|customiz|centraliz)\w*"
    r"|behaviors?|colors?|favor\w*|honors?|centers?|defense|catalogs?)\b", re.I)
SIGN_OFFS = ("kind regards,", "best wishes,")


def _paragraphs(body: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]


def quality_checks(draft: EmailDraft, first_name: str, greeting_line: str | None = None,
                   sender_name: str | None = None, company: str | None = None) -> list[dict]:
    """Named structure checks for one email, each {"check", "ok", "detail"}.
    Expected structure: greeting / Hope you're doing well / hook naming the company /
    pain + Sprinto outcome / one question / sign-off with the sender's name."""
    body = draft.body.strip()
    norm = body.replace("’", "'")
    paras = _paragraphs(norm)
    words = len(body.split())
    sign_idx = next((i for i, p in enumerate(paras) if p.lower().startswith(SIGN_OFFS)), None)
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    if greeting_line:
        add("greeting", body.startswith(greeting_line), f'expected "{greeting_line}"')
    else:
        add("greeting", re.match(rf"^(Good (morning|afternoon|evening)|Hi|Hello|Dear) {re.escape(first_name)},", body),
            "time-of-day greeting with first name")
    add("wellbeing_line", len(paras) > 1 and paras[1].lower().startswith("hope you're doing well"),
        'second line "Hope you\'re doing well."')
    if company:
        hook = paras[2] if len(paras) > 2 else ""
        add("hook_names_company", company.split()[0].lower() in hook.lower(), "first content line names the company")
    q = body.count("?")
    add("one_question", q == 1, f"{q} question marks")
    cta_ok = sign_idx is not None and sign_idx >= 1 and "?" in paras[sign_idx - 1]
    add("question_before_sign_off", cta_ok, "the question is the last line before the sign-off")
    first_sender = (sender_name or "").split(",")[0].split()[0].lower() if sender_name else ""
    add("sign_off", sign_idx is not None and (not first_sender or first_sender in paras[sign_idx].lower()),
        'ends "Kind regards," / "Best wishes," and the sender name')
    add("length", 75 <= words <= 125, f"{words} words (target 75-125)")
    us = sorted({m.group(0) for m in US_SPELLINGS.finditer(body)})
    add("uk_spelling", not us, f"US spellings: {', '.join(us)}" if us else "")
    add("no_placeholders_or_banned", not BANNED.search(body + draft.subject), "")
    return checks


def quality_summary(checks: list[dict]) -> dict:
    failed = [c["check"] for c in checks if not c["ok"]]
    return {"passed": len(checks) - len(failed), "total": len(checks), "failed": failed, "checks": checks}


def _guidance_block(guidance: list[dict] | None, exemplars: list[dict] | None) -> str:
    out = ""
    if guidance:
        lines = [f"{i}. (outcome score {g['outcome_score']:.2f}, {g['scope']}) {g['guidance']}"
                 for i, g in enumerate(guidance, 1)]
        out += ("\n\nLessons from past outcomes, ranked best first. Apply the ones relevant to this "
                "recipient; if two conflict, follow the higher-ranked one:\n" + "\n".join(lines))
    if exemplars:
        ex = "\n---\n".join(f"Subject: {e['subject']}\n{e['body']}" for e in exemplars)
        out += ("\n\nEmails that earned positive replies (tone and structure reference only - never "
                f"reuse their facts, names or companies):\n---\n{ex}\n---")
    return out


def draft_email(profile: CompanyProfile, poc: dict, pains: PainPoints, sender_name: str,
                max_attempts: int = 2, guidance: list[dict] | None = None,
                exemplars: list[dict] | None = None, soft_retry: bool = True) -> tuple[EmailDraft, list[str]]:
    """soft_retry=False (fast mode): retry only when a hard check fails, not to polish soft ones."""
    hello = greeting(poc["first_name"])
    base = (f"Recipient: {poc['first_name']} {poc.get('last_name','')}, {poc.get('title')} at "
            f"{profile.company_name}\nSender name: {sender_name}\n"
            f"Greeting line to open with: {hello}\n\n"
            f"Pain points (use #1 as the main angle):\n{json.dumps(pains.model_dump(), indent=1)}\n\n"
            f"Research:\n{profile.model_dump_json(indent=1)}" + _guidance_block(guidance, exemplars))
    feedback = ""
    for attempt in range(max_attempts):
        draft = ask(EmailDraft, EMAIL_SYSTEM, base + feedback)
        problems = validate(draft, poc["first_name"], hello)
        soft = [c for c in quality_checks(draft, poc["first_name"], hello, sender_name, profile.company_name)
                if not c["ok"]]
        if not problems and (not soft or not soft_retry):
            return draft, []
        if attempt + 1 < max_attempts:
            fix = problems + [f"{c['check']}: {c['detail']}" for c in soft]
            feedback = f"\n\nYour previous draft failed these checks, fix them: {fix}\nPrevious draft:\n{draft.body}"
    return draft, problems          # only hard failures block sending; soft ones are reported


# ---- follow-ups when there is no reply (Gmail mode) ---------------------------
class FollowUp(BaseModel):
    body: str = Field(description="The full email body, plain text")
    value_angle: str = Field(description="The value proposition this follow-up uses, in a few words")
    engagement_ask: str = Field(description="What it asks the reader to do, in a few words")


FOLLOWUP_PLAYBOOK = {
    1: "A short, friendly nudge. Bring ONE new benefit not used before (e.g. ready-made policies "
       "and security training, or continuous monitoring after the audit) and ask a simple yes/no question.",
    2: "Lead with something useful. Offer a concrete resource drawn only from what Sprinto does "
       "(e.g. a short checklist of what a SOC 2 or ISO 27001 audit asks for) and ask whether they would "
       "like it sent over. A different angle and a different ask from every earlier email.",
    3: "A polite final note. Acknowledge they are busy, say this is the last email, give an easy way out, "
       "and ask whether someone else at the company looks after security compliance.",
}

SCHEDULING_PLAYBOOK = {
    1: "They said they were interested and we offered meeting times, then went quiet. A short, friendly nudge: "
       "ask whether any of the times suit, or offer to find new ones. Do not re-pitch.",
    2: "Offer flexibility: ask them to name a time that suits, or a better week. One short line on what the call "
       "will cover. A different ask from the first nudge.",
    3: "A polite last note on scheduling: no pressure, happy to pick it up whenever suits, and ask whether a "
       "colleague should join or take the call instead.",
}

FOLLOWUP_SYSTEM = f"""You write a follow-up to an email from Sprinto's Growth Lead that got no reply.
{SPRINTO_CONTEXT}

Style: UK business English - polite, warm, brief. UK spelling.
Rules:
- Open with exactly the greeting line you are given, then a blank line, then "Hope you're doing well."
- Never repeat a value proposition, phrase, hook or ask already used in the earlier emails (given).
- Never invent facts, numbers, customers or Sprinto claims beyond the Sprinto description above.
- 50-100 words including the greeting. Exactly one question.
- Sign off with "Kind regards," on its own line, then the sender name given (name and company on
  separate lines)."""


def follow_up(n: int, first_name: str, title: str | None, company: str, research: dict,
              previous_emails: list[str], sender_name: str, max_attempts: int = 2, stage: str = "cold") -> FollowUp:
    """stage "cold": no reply to the cold email; stage "scheduling": they were interested, then went quiet."""
    hello = greeting(first_name)
    book = SCHEDULING_PLAYBOOK if stage == "scheduling" else FOLLOWUP_PLAYBOOK
    base = (f"This is follow-up {n} of {len(book)}. Approach for this one: {book[n]}\n\n"
            f"Recipient: {first_name}, {title or 'unknown role'} at {company}\nSender name: {sender_name}\n"
            f"Greeting line to open with: {hello}\n\nResearch:\n{json.dumps(research, indent=1)}\n\n"
            "Earlier emails in this thread (do not repeat their angles or asks):\n"
            + "\n---\n".join(previous_emails))
    feedback = ""
    for attempt in range(max_attempts):
        fu = ask(FollowUp, FOLLOWUP_SYSTEM, base + feedback)
        d = EmailDraft(subject="", body=fu.body, hook_used=fu.value_angle, reasoning=fu.engagement_ask)
        bad = [c for c in quality_checks(d, first_name, hello, sender_name) if not c["ok"] and c["check"] != "length"]
        words = len(fu.body.split())
        if not bad and 40 <= words <= 120:
            return fu
        if attempt + 1 < max_attempts:
            feedback = (f"\n\nFix these problems: {[c['check'] + ': ' + c['detail'] for c in bad]}"
                        f"{'' if 40 <= words <= 120 else f'; length {words} words (want 50-100)'}\n"
                        f"Previous draft:\n{fu.body}")
    return fu
