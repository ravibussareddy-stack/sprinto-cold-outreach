"""Judgement questions: "I found X, my rules don't say how to treat it - how should I read this from now on?"

Facts are never asked: missing facts are researched (qualify.deep_cert_urls). A question is only raised
for a situation the rules do not settle, it is asked ONCE (not per company: later companies are added
as examples), and your answer becomes a standing ruling applied to every future company. Until you
answer, the current cautious behaviour (the default) applies and nothing waits.

Known situations (TOPICS) have options with a fixed effect in code. New situations the model notices
while reading a site (CompanyProfile.unusual_finding) get a free-text ruling, applied on later
companies with the same finding by a short model call (apply_open_ruling). At most MAX_OPEN_NEW new
situations are open at once.
"""
import datetime as dt
import re

import db

MAX_OPEN_NEW = 5

TOPICS = {
    "soc2_type1_only": {
        "title": "SOC 2 Type I only",
        "question": "Should a company with only a SOC 2 Type I report count as certified?",
        "why": "Type I checks controls at one point in time; enterprise buyers usually ask for Type II next.",
        "options": [{"value": "certified", "label": "Yes, certified (dormant)", "effect": "Type I counts as SOC 2: the company is dormant."},
                    {"value": "prospect", "label": "No, still a prospect", "effect": "Type I does not count: the company is scored, with Type II as the pitch."}],
        "default": "certified"},
    "badge_only": {
        "title": "Certificate shown only as a badge",
        "question": "A certificate appears only as a badge or bare label, with no statement that the company holds it. Should it count?",
        "why": "Badges usually mean the company holds it, but sometimes list frameworks a product supports.",
        "options": [{"value": "count", "label": "Count badges", "effect": "Bare badges count as held."},
                    {"value": "clear_only", "label": "Only clear statements", "effect": "Only sentences like 'we are SOC 2 certified' count."}],
        "default": "count"},
    "uses_platform": {
        "title": "Already uses a compliance platform, no certificate yet",
        "question": "A company already runs Vanta, Drata or a similar platform but holds no certificate yet. How should it be treated?",
        "why": "They have bought a competitor's tool; some will switch, many will not.",
        "options": [{"value": "switch", "label": "Switch prospect (flagged)", "effect": "Scored as a prospect, flagged: pitch a switch."},
                    {"value": "skip", "label": "Skip them", "effect": "Disqualified: they have already chosen a platform."}],
        "default": "switch"},
    "acquired": {
        "title": "Acquired company",
        "question": "A company says it has been acquired by, or joined, another company. Should it still be a prospect?",
        "why": "Compliance is often decided by the parent, but some acquisitions stay independent.",
        "options": [{"value": "skip", "label": "Skip acquired companies", "effect": "Disqualified: the parent decides compliance."},
                    {"value": "prospect", "label": "Treat as a prospect", "effect": "Scored like any other company."}],
        "default": "skip"},
    "sells_compliance": {
        "title": "Sells compliance or security software itself",
        "question": "A company sells compliance, GRC or security-questionnaire software itself. Is it a prospect or a competitor?",
        "why": "Adjacent vendors need their own certificates, but direct competitors will not buy.",
        "options": [{"value": "prospect", "label": "Prospect", "effect": "Scored like any other company."},
                    {"value": "skip", "label": "Competitor: skip", "effect": "Disqualified as a competitor."}],
        "default": "prospect"},
    "old_report": {
        "title": "Only an old certification report",
        "question": "The only certificate evidence is dated two or more years ago (e.g. 'our 2021 SOC 2 report'). Does it still count?",
        "why": "Reports are renewed yearly; an old date may mean it lapsed, or just an old page.",
        "options": [{"value": "held", "label": "Still counts", "effect": "Old reports count as held: dormant."},
                    {"value": "lapsed", "label": "Treat as lapsed", "effect": "Old reports do not count: the company is scored."}],
        "default": "held"},
}

_cache: dict | None = None


def load() -> dict:
    """All answered rulings {topic: answer}, read once per run (qualify calls this at the start)."""
    global _cache
    _cache = {}
    if db.enabled():
        for key, ans in (db.answered_judgments() or []):
            _cache[key.split(":", 1)[1]] = ans
    return _cache


def ruling(topic: str):
    r = (_cache if _cache is not None else load()).get(topic)
    return TOPICS[topic]["default"] if (r is None and topic in TOPICS) else r


def answered(topic: str) -> bool:
    return topic in (_cache if _cache is not None else load())


# ---------------------------------------------------------------- detectors (evidence the run already has)
TYPE1 = re.compile(r"type\s?(1|i)\b", re.I)
TYPE2 = re.compile(r"type\s?(2|ii)\b", re.I)
SELLS = re.compile(r"compliance automation|\bgrc\b|governance,? risk|security questionnaire|trust (center|centre) (software|platform)|"
                   r"vendor risk|soc ?2 (automation|software|platform)|audit readiness", re.I)


def soc2_type1_only(evidence: list[dict]) -> dict | None:
    soc = [e for e in evidence if e["cert"] == "SOC 2" and e["who"] in ("self", "unclear")]
    if soc and any(TYPE1.search(e["sentence"]) for e in soc) and not any(TYPE2.search(e["sentence"]) for e in soc):
        e = next(e for e in soc if TYPE1.search(e["sentence"]))
        return {"finding": "only a SOC 2 Type I report", "quote": e["sentence"], "url": e.get("url")}
    return None


def badge_only(evidence: list[dict], model_claims: list[str], core_held: set[str]) -> dict | None:
    for fam in sorted(core_held):
        ev = [e for e in evidence if e["cert"] == fam]
        if ev and all(e["who"] == "unclear" for e in ev) and not any(fam.lower().replace(" ", "") in c.lower().replace(" ", "") for c in model_claims):
            return {"finding": f"{fam} shown only as a badge or label", "quote": ev[0]["sentence"], "url": ev[0].get("url")}
    return None


def old_report(evidence: list[dict]) -> dict | None:
    year = dt.date.today().year
    held = [e for e in evidence if e["who"] == "self" and e["cert"] in ("SOC 2", "ISO 27001")]
    if not held:
        return None
    # A year straight after a standard number is its version ("ISO/IEC 27001:2022"), not a report date.
    years = [int(y) for e in held for y in re.findall(r"(?<![:/\-\d])\b(20[12]\d)\b", e["sentence"])]
    if years and max(years) <= year - 2:
        e = held[0]
        return {"finding": f"certificate evidence dated {max(years)}", "quote": e["sentence"], "url": e.get("url")}
    return None


def detect(profile, evidence: list[dict], core_held: set[str], tools: list[dict]) -> list[dict]:
    """Situations in this company the rules leave to your judgement: [{topic, finding, quote, url}]."""
    out = []
    for topic, hit in (("soc2_type1_only", soc2_type1_only(evidence)),
                       ("badge_only", badge_only(evidence, profile.certifications_claimed, core_held)),
                       ("old_report", old_report(evidence))):
        if hit:
            out.append({"topic": topic, **hit})
    if tools and not core_held:
        t = tools[0]
        out.append({"topic": "uses_platform", "finding": f"uses {t['tool']}, no certificate yet", "quote": t.get("sentence"), "url": t.get("url")})
    if profile.acquired_by or profile.funding_stage == "Acquired":
        out.append({"topic": "acquired", "finding": f"now part of {profile.acquired_by or 'another company'}", "quote": None, "url": None})
    what = " ".join([profile.one_line_description or "", profile.buyer_type or ""] + profile.target_industries)
    m = SELLS.search(what)
    if m:
        out.append({"topic": "sells_compliance", "finding": f"sells {m.group(0)} software", "quote": profile.one_line_description, "url": None})
    uf = getattr(profile, "unusual_finding", None)
    if uf and uf.topic and uf.topic not in TOPICS:
        out.append({"topic": "new:" + slug(uf.topic), "finding": uf.finding, "quote": uf.quote, "url": None, "question": uf.question})
    return out


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:48]


def open_rulings() -> list[dict]:
    """New situations already raised (answered or open), so the model reuses their topic names."""
    return (db.judgment_topics() or []) if db.enabled() else []


# ---------------------------------------------------------------- asking
def ask(domain: str, company: str, findings: list[dict]):
    """Raise (or add an example to) a question for every situation without a ruling. Never blocks."""
    if not db.enabled():
        return
    open_new = None
    for f in findings:
        topic = f["topic"]
        if answered(topic):
            continue
        example = {"domain": domain, "company": company, "finding": f["finding"], "quote": (f.get("quote") or "")[:300],
                   "url": f.get("url"), "seen": dt.date.today().isoformat()}
        if topic in TOPICS:
            t = TOPICS[topic]
            db.ask_judgment(f"judgment:{topic}", "judgment", t["question"],
                            f"{t['why']} Until you answer, the default is used: "
                            f"{next(o['label'] for o in t['options'] if o['value'] == t['default'])}.", t["options"], example)
        else:
            if open_new is None:
                open_new = db.open_new_judgments() or 0
            if open_new >= MAX_OPEN_NEW and not db.question_exists(f"judgment:{topic}"):
                continue                                # cap: no more than MAX_OPEN_NEW new situations waiting
            q = f.get("question") or f"I found {f['finding']} at {company}. How should I treat this from now on?"
            db.ask_judgment(f"judgment:{topic}", "judgment_open", q,
                            "A situation my rules do not cover. Your answer is kept as a ruling and applied to every "
                            "later company with the same finding. Until then nothing changes for it.", None, example)
            open_new += 1


# ---------------------------------------------------------------- applying a free-text ruling
def apply_open_ruling(profile, decision: str, reason: str, findings: list[dict]) -> tuple[str, str] | None:
    """A company has a new-situation finding you have ruled on: let a short model call apply your ruling.
    Returns (decision, reason) to use instead, or None for no change."""
    rulings = [(f, (_cache or {}).get(f["topic"])) for f in findings if f["topic"].startswith("new:")]
    rulings = [(f, r) for f, r in rulings if isinstance(r, str) and r.strip()]
    if not rulings:
        return None
    from typing import Literal

    from pydantic import BaseModel, Field

    from llm import ask as llm_ask

    class Applied(BaseModel):
        decision: Literal["unchanged", "DORMANT", "DISQUALIFIED", "QUALIFIED_FLAGGED", "QUALIFIED"]
        reason: str = Field(description="One sentence quoting the ruling it applied")

    f, r = rulings[0]
    out = llm_ask(Applied, "You apply a sales lead's standing ruling to one company. Use only the ruling and the "
                  "facts given. If the ruling does not clearly change the decision, answer unchanged.",
                  f"Ruling for situations like '{f['finding']}': {r}\n\nThis company: {profile.company_name} - "
                  f"{profile.one_line_description}\nFinding here: {f['finding']} ({f.get('quote') or ''})\n"
                  f"Current decision: {decision} ({reason})", thinking="low", temperature=0.0)
    if out.decision == "unchanged" or out.decision == decision:
        return None
    return out.decision, f"Your ruling applied: {out.reason}"
