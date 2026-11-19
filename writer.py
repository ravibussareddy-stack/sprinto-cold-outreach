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
time to evidence collection; a CEO loses deals stuck in security review; a
security lead drowns in questionnaires and spreadsheets). No generic pains.
For each pain, also say who THEIR customers or buyers are (named customers,
industries, buyer type from the research) and what those customers will expect
(customer_angle), and what fixing it does for their business (business_outcome).

Separate fact from hypothesis:
- evidence_from_research: a fact the profile actually states, quoted closely, with
  its source URL in source_url when the profile's evidence gives one.
- why_this_person_feels_it: the sales hypothesis, worded as likely ("often",
  "tends to", "likely"). Never state durations, effort or costs ("weeks of manual
  work", "hours a week") unless the research says so.
- If compliance_tool_used is set, they already run a compliance platform: the
  pain is what that tool leaves undone, not a first audit.
- If expansion_frameworks is set, they ALREADY hold SOC 2 / ISO 27001: pain point #1
  is the new framework named there (why they now need it), never a first audit.
- If certification_status is "unknown", nobody knows whether they hold SOC 2 or ISO 27001:
  frame pain point #1 as what enterprise buyers will ask for, never as "you lack it"."""

SPRINTO_EDGE = """Sprinto's edge (use the one or two that fit THIS company; never more than the description says):
- connects to their cloud, HR, code and identity tools, so evidence is collected automatically, not in spreadsheets
- monitors controls continuously, so they stay compliant between audits ("always on")
- ready-made policies and security training, so a small team is not writing them from scratch
- audit-ready in weeks rather than months, with far less engineering time
- helps answer security questionnaires, so procurement reviews stop stalling deals
- several frameworks (SOC 2, ISO 27001, GDPR, HIPAA and more) from one place"""

EMAIL_EXAMPLES = """Example 1 (CTO, Series A fintech API; hook: new enterprise plan; sells to banks)
Subject: Ledgerly's enterprise plan and bank security reviews

Good morning Priya,

Hope you're doing well.

Congratulations on launching Ledgerly's Enterprise plan. Banks evaluating a payments API usually send a long security questionnaire and ask for a SOC 2 report before procurement moves.

Sprinto connects to your cloud, code and identity tools and collects that evidence automatically, so your engineers are not rebuilding it for every bank review. It also helps answer the questionnaires themselves, and gets companies audit-ready in weeks rather than months.

For Ledgerly, that could mean bank deals that move through procurement instead of waiting on it. Would a 15-minute call next week be useful?

Kind regards,
Sarah Mitchell
Sprinto

Example 2 (Head of Security, Series B health-tech; hook: open compliance role; sells to hospitals)
Subject: Fernwood Health's compliance hire and hospital buyers

Good afternoon Tom,

Hope you're doing well.

I noticed Fernwood Health is hiring its first Compliance Manager. Hospital buyers tend to expect HIPAA-ready controls and a clean audit trail before they sign.

Sprinto gives that hire a head start: ready-made policies and security training, continuous monitoring of your controls, and evidence kept audit-ready across HIPAA and SOC 2 from one place, rather than in spreadsheets.

That way the role can focus on the hospital programme, not the paperwork. Might it be worth a brief conversation before they start?

Best wishes,
Sarah Mitchell
Sprinto"""

EMAIL_SYSTEM = f"""You write cold emails for Sprinto's Growth Lead that real
people answer. {SPRINTO_CONTEXT}

{SPRINTO_EDGE}

Style: UK business English - polite, warm, specific and to the point. UK spelling
(organisation, prioritise, programme). Confident, never pushy.

Structure (exactly this, three short body paragraphs, 90-140 words in all):
- Line 1: exactly the greeting line you are given (e.g. "Good morning Priya,"),
  then a blank line, then "Hope you're doing well." on its own line.
- Paragraph 1 - THEIR world: a specific, true fact about the company from the
  research (a named customer, product, market, funding, open role), then what
  THEIR customers or buyers (name the segment or industry they sell to) will
  expect from them on security or compliance. Use the pain point's customer_angle.
- Paragraph 2 - Sprinto's edge: the one or two Sprinto capabilities that fit
  this company's situation, said concretely and tied to their stack or team.
- Paragraph 3 - their business outcome (the pain point's business_outcome: e.g.
  deals with those customers moving through security reviews faster), then ONE
  soft question as the CTA, UK style ("Would a 15-minute call next week be useful?").
- The swap test: if a sentence would fit any other company unchanged, rewrite it
  with their name, their customers or their product.
- Plain text. No bullet lists, no emojis, no buzzwords ("synergy", "leverage",
  "revolutionise"), no fake familiarity, no one-sentence filler paragraphs.
- If certification_status is "unknown" (no certification information found), this is a
  discovery email: never say or imply they lack SOC 2 / ISO 27001. Make the single question
  ask whether it is on their roadmap, e.g. "If SOC 2 or ISO 27001 is on your roadmap this
  year, would a 15-minute call be useful?"
- If the research says an offerings deck is attached, you may mention it once, briefly
  (e.g. "I've attached a short overview of what we cover"). Never describe what is in it.
- If the research lists expansion_frameworks, the company is already certified: the
  angle is that new framework (built on the controls they already run), never a first audit.
- Never invent facts, numbers or customer names not in the research. Never state
  how long something takes them or how much effort it costs ("weeks of manual
  work") unless the research says so; hedge or ask instead.
- Describe Sprinto only with what the Sprinto description above says. No new
  numbers, time savings or claims such as "weeks to hours".
- Sign off with "Kind regards," on its own line (unless the sender's standing
  instructions name another sign-off), then the sender name given.
- Subject: 3-8 words, sentence case (capital first letter), names the company
  and the specific hook, and reads as relevant to their industry. Never generic
  ("Security questionnaires for enterprise deals" is too generic; "Atlan's
  Mastercard and Nasdaq security reviews" is right).

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


def _portfolio_note() -> str:
    try:
        import frameworks
        return frameworks.portfolio_note()
    except Exception:
        return ""


def pain_points(profile: CompanyProfile, poc: dict) -> PainPoints:
    user = (f"Contact: {poc['first_name']} {poc.get('last_name','')}, {poc.get('title')}\n\n"
            f"Company profile (JSON):\n{profile.model_dump_json(indent=1)}")
    return ask(PainPoints, PAIN_SYSTEM + _portfolio_note(), user, thinking="low")   # the critic polices the email


def validate(draft: EmailDraft, first_name: str, greeting_line: str | None = None) -> list[str]:
    problems = []
    words = len(draft.body.split())
    if first_name.lower() not in draft.body.lower()[:60]:
        problems.append("does not greet the contact by first name")
    if greeting_line and not draft.body.strip().startswith(greeting_line):
        problems.append(f'does not open with "{greeting_line}"')
    if greeting_line and "hope you're doing well" not in draft.body.lower().replace("’", "'"):
        problems.append('missing "Hope you\'re doing well." line')
    if not 70 <= words <= 170:
        problems.append(f"length {words} words (want 90-140)")
    if BANNED.search(draft.body + draft.subject):
        problems.append("contains banned phrase or placeholder bracket")
    if draft.body.count("?") < 1:
        problems.append("no question CTA")
    return problems


US_SPELLINGS = re.compile(
    r"\b(\w*(?:organiz|prioritiz|optimiz|analyz|realiz|recogniz|minimiz|maximiz|standardiz|customiz|centraliz)\w*"
    r"|behaviors?|colors?|favor\w*|honors?|centers?|defense|catalogs?)\b", re.I)
SIGN_OFF_RE = re.compile(r"^(kind|best|warm|warmest|many)?\s*(regards|wishes|thanks)\b[,!.]?", re.I)
KNOWN_SIGN_OFFS = ("best regards", "kind regards", "warm regards", "warmest regards", "best wishes",
                   "many thanks", "regards", "thanks")
DEFAULT_SIGN_OFF = "Kind regards,"


def preferred_sign_off(guidance: list[dict] | None) -> str:
    """The sign-off the feedback repo asks for (highest-ranked item that names one), else "Kind regards,".
    Used by templated emails (proposals, confirmations) that are not written by the model."""
    for g in guidance or []:
        text = g.get("guidance", "").lower()
        found = sorted((text.find(k), k) for k in KNOWN_SIGN_OFFS if k in text)
        if found:
            return found[0][1].capitalize() + ","
    return DEFAULT_SIGN_OFF


def fix_sign_off(body: str, sender_name: str) -> str:
    """Whatever the model wrote after the sign-off line, end with the exact sender lines
    ("Ravi Bussareddy, Sprinto" -> "Ravi Bussareddy\nSprinto")."""
    lines = (body or "").rstrip().splitlines()
    idx = next((i for i in range(len(lines) - 1, -1, -1)
                if SIGN_OFF_RE.match(lines[i].strip()) and len(lines[i].strip()) < 25), None)
    if idx is None or not sender_name:
        return body
    return "\n".join(lines[:idx + 1] + sender_lines(sender_name))


def sender_lines(sender_name: str) -> list[str]:
    """"Ravi Bussareddy, Sprinto" -> ["Ravi Bussareddy", "Sprinto"]; the company is added if missing."""
    who = [x.strip() for x in (sender_name or "").split(",") if x.strip()]
    if not any("sprinto" in x.lower() for x in who):     # SENDER_NAME may be just the person's name
        who.append("Sprinto")
    return who


def fix_subject(subject: str) -> str:
    s = (subject or "").strip().strip('"')
    return s[:1].upper() + s[1:]


def _paragraphs(body: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]


CAPABILITY = re.compile(r"\b(cloud|identity|hr|code|monitor\w*|evidence|polic(y|ies)|training|questionnaire\w*|"
                        r"audit-ready|always on|continuous\w*|frameworks?)\b", re.I)
OUTCOME = re.compile(r"\b(deals?|procurement|sales cycle|close|closing|win|buyers?|customers?|clients?|contracts?|"
                     r"reviews?|enterprise|trust|onboard\w*|renewals?)\b", re.I)
STOP = {"the", "and", "for", "with", "teams", "team", "companies", "company", "businesses", "business", "software",
        "services", "service", "platform", "solutions", "mid-size", "small", "large", "their", "other"}


def market_terms(profile) -> set[str]:
    """Words that name THIS company's market: industries, buyer type, named customers."""
    if profile is None:
        return set()
    words = set()
    for t in list(profile.target_industries or []) + list(profile.named_customers or [])[:6] + [profile.buyer_type or ""]:
        for w in re.findall(r"[A-Za-z][A-Za-z.&-]{3,}", t or ""):
            if w.lower() not in STOP:
                words.add(w.lower())
    return words


def quality_checks(draft: EmailDraft, first_name: str, greeting_line: str | None = None,
                   sender_name: str | None = None, company: str | None = None, profile=None) -> list[dict]:
    """Named structure checks for one email, each {"check", "ok", "detail"}.
    Expected structure: greeting / Hope you're doing well / hook naming the company /
    pain + Sprinto outcome / one question / sign-off with the sender's name."""
    body = draft.body.strip()
    norm = body.replace("’", "'")
    paras = _paragraphs(norm)
    words = len(body.split())
    sign_idx = next((i for i, p in enumerate(paras) if SIGN_OFF_RE.match(p)), None)
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
        'ends with a sign-off ("Kind regards,") and the sender name')
    subj = (draft.subject or "").strip()
    if subj:                                     # follow-ups have no subject of their own
        add("subject_capitalised", subj[:1].isupper(), f'"{subj}"')
        if company:
            add("subject_names_company", company.split()[0].lower().strip(",.") in subj.lower(),
                "the subject names the company")
    add("length", 90 <= words <= 150, f"{words} words (target 90-140)")
    if company or profile is not None:                  # specific and persuasive, not just well formed
        terms = market_terms(profile)
        hits = sorted({t for t in terms if re.search(rf"\b{re.escape(t)}\b", norm, re.I)})
        add("names_their_market", not terms or bool(hits),
            f"mentions {', '.join(hits[:3])}" if hits else "say who they sell to (their industry, buyers or named customers)")
        caps = {m.group(0).lower() for m in CAPABILITY.finditer(norm)}
        add("sprinto_edge", len(caps) >= 2, f"names {len(caps)} Sprinto capabilities (want 2+)")
        add("business_outcome", bool(OUTCOME.search(paras[-2] if len(paras) > 2 else norm)) if paras else False,
            "the last paragraph says what it means for their deals or customers")
    if draft.critique:
        c = draft.critique
        add("not_generic", c.get("ok", True), c.get("summary", ""))
    us = sorted({m.group(0) for m in US_SPELLINGS.finditer(body)})
    add("uk_spelling", not us, f"US spellings: {', '.join(us)}" if us else "")
    add("no_placeholders_or_banned", not BANNED.search(body + draft.subject), "")
    return checks


def quality_summary(checks: list[dict]) -> dict:
    failed = [c["check"] for c in checks if not c["ok"]]
    return {"passed": len(checks) - len(failed), "total": len(checks), "failed": failed, "checks": checks}


def instructions_block(guidance: list[dict] | None) -> str:
    """Feedback-repo items as standing instructions for the SYSTEM prompt. They override the
    default style rules (e.g. a different sign-off), never the facts-only rules."""
    if not guidance:
        return ""
    lines = [f"{i}. ({g['scope']}, outcome score {g['outcome_score']:.2f}) {g['guidance']}"
             for i, g in enumerate(guidance, 1)]
    return ("\n\nSENDER'S STANDING INSTRUCTIONS (from the feedback repo, ranked best first). Follow every "
            "one that applies to this email. They OVERRIDE the default style rules above (sign-off, "
            "subject, tone, wording); they never permit inventing facts. If two conflict, follow the "
            "higher-ranked one:\n" + "\n".join(lines))


def _guidance_block(guidance: list[dict] | None, exemplars: list[dict] | None) -> str:
    out = ""
    if exemplars:
        ex = "\n---\n".join(f"Subject: {e['subject']}\n{e['body']}" for e in exemplars)
        out += ("\n\nEmails that earned positive replies (tone and structure reference only - never "
                f"reuse their facts, names or companies):\n---\n{ex}\n---")
    return out


class Critique(BaseModel):
    specific: int = Field(description="1-5: how specific to THIS company (5 = could not be sent to anyone else)")
    customer_value: int = Field(description="1-5: how clearly it says what this means for their customers or deals")
    sprinto_edge: int = Field(description="1-5: how clearly it shows why Sprinto, tied to their situation")
    generic_sentences: list[str] = Field([], description="Sentences that would fit any company unchanged (verbatim)")
    fix: str = Field(description="One or two concrete instructions to make it specific and persuasive; empty if fine")


CRITIC_SYSTEM = """You are a demanding sales manager reviewing one cold email before it goes out.
Judge only specificity and value, not grammar. An email that names the company but could otherwise
be sent to anyone is generic. Use the research to suggest what to say instead.
Ignore the required lines - the greeting, "Hope you're doing well.", the sign-off, a one-line
mention of an attached overview, and the closing question: they are fixed by house style. A
factual description of what Sprinto does is fine when it is tied to this company's situation."""


def critique(draft: EmailDraft, profile: CompanyProfile, pains: PainPoints) -> dict:
    """Quick critic (low thinking): is it specific to the company, and does it say why Sprinto helps
    THEIR customers and business? ok = all three scores 4+ and no generic sentences."""
    c = ask(Critique, CRITIC_SYSTEM, f"Research:\n{profile.model_dump_json()}\n\nPain points:\n"
            f"{json.dumps(pains.model_dump())}\n\nEmail:\nSubject: {draft.subject}\n{draft.body}",
            thinking="low", temperature=0.0, max_output_tokens=1500)
    required = re.compile(r"hope you're doing well|attached|roadmap|15-minute|call next week|brief conversation|regards|wishes", re.I)
    generic = [g for g in c.generic_sentences if not required.search(g.replace("\u2019", "'"))]
    ok = c.specific >= 4 and c.customer_value >= 4 and c.sprinto_edge >= 3 and len(generic) <= 1
    c.generic_sentences = generic
    return {"ok": ok, "scores": {"specific": c.specific, "customer_value": c.customer_value, "sprinto_edge": c.sprinto_edge},
            "generic_sentences": c.generic_sentences, "fix": c.fix,
            "summary": (f"specific {c.specific}/5, value for their customers {c.customer_value}/5, Sprinto's edge "
                        f"{c.sprinto_edge}/5" + (f"; generic: {c.generic_sentences[0][:80]}" if c.generic_sentences else ""))}


def draft_email(profile: CompanyProfile, poc: dict, pains: PainPoints, sender_name: str,
                max_attempts: int = 2, guidance: list[dict] | None = None,
                exemplars: list[dict] | None = None, soft_retry: bool = True,
                deck_attached: bool = False) -> tuple[EmailDraft, list[str]]:
    """soft_retry=False: retry only when a hard check fails, not to polish soft ones."""
    hello = greeting(poc["first_name"])
    base = (f"Recipient: {poc['first_name']} {poc.get('last_name','')}, {poc.get('title')} at "
            f"{profile.company_name}\nSender name: {sender_name}\n"
            f"Greeting line to open with: {hello}\n"
            f"Offerings deck attached: {'yes (a short PDF overview of what Sprinto covers)' if deck_attached else 'no'}\n\n"
            f"Pain points (use #1 as the main angle):\n{json.dumps(pains.model_dump(), indent=1)}\n\n"
            f"Research:\n{profile.model_dump_json(indent=1)}" + _guidance_block(guidance, exemplars))
    system = EMAIL_SYSTEM + _portfolio_note() + instructions_block(guidance)
    feedback = ""
    for attempt in range(max_attempts):
        # The first draft thinks fully; a rewrite follows an explicit critique, so the quick setting is enough.
        draft = ask(EmailDraft, system, base + feedback, thinking="low" if attempt else None)
        draft.subject = fix_subject(draft.subject)
        draft.body = fix_sign_off(draft.body, sender_name)
        problems = validate(draft, poc["first_name"], hello)
        soft = [c for c in quality_checks(draft, poc["first_name"], hello, sender_name, profile.company_name, profile)
                if not c["ok"]]
        if not problems and not soft:
            # Well formed and specific on paper: now the critic asks "could this go to any company?"
            try:
                draft.critique = critique(draft, profile, pains)
            except Exception as e:                       # the critic must never block a draft
                draft.critique = {"ok": True, "summary": f"critic unavailable ({type(e).__name__})"}
            if draft.critique["ok"]:
                return draft, []
            soft = [{"check": "not_generic", "detail": draft.critique["summary"] + ". " + draft.critique.get("fix", "")}]
        if not problems and (not soft or not soft_retry):
            return draft, []
        if attempt + 1 < max_attempts:
            fix = problems + [f"{c['check']}: {c['detail']}" for c in soft]
            generic = (draft.critique or {}).get("generic_sentences") or []
            feedback = (f"\n\nYour previous draft failed these checks, fix them: {fix}"
                        + (f"\nRewrite these generic sentences with this company's specifics: {generic}" if generic else "")
                        + f"\nPrevious draft:\n{draft.body}")
    return draft, problems          # only hard failures block sending; soft ones (and the critic) are reported


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
- Sign off with the sign-off line given, on its own line, then the sender name given (name and
  company on separate lines)."""


def follow_up(n: int, first_name: str, title: str | None, company: str, research: dict,
              previous_emails: list[str], sender_name: str, max_attempts: int = 2, stage: str = "cold",
              guidance: list[dict] | None = None) -> FollowUp:
    """stage "cold": no reply to the cold email; stage "scheduling": they were interested, then went quiet.
    guidance: feedback-repo items (same as the cold email), so follow-ups keep the sender's style."""
    hello = greeting(first_name)
    system = FOLLOWUP_SYSTEM + instructions_block(guidance)
    book = SCHEDULING_PLAYBOOK if stage == "scheduling" else FOLLOWUP_PLAYBOOK
    base = (f"This is follow-up {n} of {len(book)}. Approach for this one: {book[n]}\n\n"
            f"Recipient: {first_name}, {title or 'unknown role'} at {company}\nSender name: {sender_name}\n"
            f"Greeting line to open with: {hello}\nSign-off line: {preferred_sign_off(guidance)}\n\nResearch:\n{json.dumps(research, indent=1)}\n\n"
            "Earlier emails in this thread (do not repeat their angles or asks):\n"
            + "\n---\n".join(previous_emails))
    feedback = ""
    for attempt in range(max_attempts):
        fu = ask(FollowUp, system, base + feedback)
        fu.body = fix_sign_off(fu.body, sender_name)
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
