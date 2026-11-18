"""Disagreement score for a drafted email: how far the draft is from 'intact'.

Build 1 (now): computed and reported only. Nothing is held back.
Build 2 (later): drafts at or above HOLD_THRESHOLD are held for a human. The threshold
is set from the Growth Lead's review data (review.py), not guessed.

Three independent signals, each 0..1, weighted into a 0..100 score (higher = more disagreement):
  structure  (25): the email's shape is off - failed quality checks (greeting, "Hope you're
                   doing well", hook naming the company, one question, sign-off, UK spelling,
                   length, placeholders). Full weight at 3 or more failures.
  wrong_info (45): share of factual claims that are not supported - claims about the company
                   or recipient checked against the research, claims about Sprinto checked
                   against the Sprinto description (an invented "weeks to hours" counts).
  judge      (30): a separate sceptical-reviewer call says whether the email is faithful.
"""
from pydantic import BaseModel, Field

from llm import ask
from writer import SPRINTO_CONTEXT

WEIGHTS = {"structure": 25, "wrong_info": 45, "judge": 30}
STRUCTURE_FULL_AT = 3
HOLD_THRESHOLD = None          # build 2 sets this from review data; None = report only


class Claim(BaseModel):
    text: str = Field(description="One factual claim made in the email, quoted or paraphrased")
    about: str = Field("company", description="'company' (the prospect or recipient) or 'sprinto'")
    supported: bool = Field(description="True only if the source explicitly supports this claim")


class ClaimCheck(BaseModel):
    claims: list[Claim] = Field(description="Every factual statement about the company, the recipient or Sprinto")


class JudgeVerdict(BaseModel):
    faithful: bool = Field(description="True only if the email invents nothing and overstates nothing")
    unsupported: list[str] = Field([], description="Statements that go beyond the research or the Sprinto description")
    reason: str


CLAIM_SYSTEM = f"""List every factual claim the email makes.
- Claims about the prospect company or the recipient (about = "company"): supported only if
  the research explicitly says so.
- Claims about what Sprinto does or achieves (about = "sprinto"): supported only if the Sprinto
  description below says so. Invented numbers or outcomes (e.g. "weeks to hours") are unsupported.
Not claims: the greeting, the recipient's own name or title (they are inputs, not research), the
sign-off, opinions, and hedged general statements ("reviews like that often take time"). A general
statement worded as a certainty about THIS company or its customers ("your customers invariably...")
IS a claim and needs support. Never assume support.

Sprinto description:
{SPRINTO_CONTEXT}"""

JUDGE_SYSTEM = f"""You are a sceptical compliance reviewer checking a cold email before it is sent.
Assume the email is wrong until the research shows otherwise. Flag any invented fact,
overstated fact, made-up customer, number or certification, or any Sprinto claim that the
Sprinto description does not support. Be strict.

Sprinto description:
{SPRINTO_CONTEXT}"""


def check_claims(email_body: str, research_json: str) -> ClaimCheck:
    user = f"Research (JSON):\n{research_json}\n\nEmail:\n{email_body}"
    return ask(ClaimCheck, CLAIM_SYSTEM, user, temperature=0.0)


def judge(email_body: str, research_json: str) -> JudgeVerdict:
    user = f"Research (JSON):\n{research_json}\n\nEmail:\n{email_body}"
    return ask(JudgeVerdict, JUDGE_SYSTEM, user, temperature=0.0)


def score(structure_failures: list[str], claims: ClaimCheck, verdict: JudgeVerdict) -> dict:
    total = len(claims.claims)
    wrong = [c for c in claims.claims if not c.supported]
    parts = {
        "structure": round(WEIGHTS["structure"] * min(len(structure_failures), STRUCTURE_FULL_AT) / STRUCTURE_FULL_AT),
        "wrong_info": round(WEIGHTS["wrong_info"] * (len(wrong) / total if total else 0.0)),
        "judge": round(WEIGHTS["judge"] * (0.0 if verdict.faithful else 1.0)),
    }
    total_score = min(100, sum(parts.values()))
    return {
        "score": total_score,
        "parts": parts,
        "structure_failures": structure_failures,
        "claims_total": total,
        "claims_unsupported": [c.text for c in wrong],
        "wrong_about_company": [c.text for c in wrong if c.about != "sprinto"],
        "wrong_about_sprinto": [c.text for c in wrong if c.about == "sprinto"],
        "judge_faithful": verdict.faithful,
        "judge_reason": verdict.reason,
        "judge_unsupported": verdict.unsupported,
        "held": HOLD_THRESHOLD is not None and total_score >= HOLD_THRESHOLD,
    }


def assess(email_body: str, research_json: str, structure_failures: list[str]) -> dict:
    """structure_failures: names of failed quality checks (writer.quality_checks) plus any
    hard check failures."""
    return score(structure_failures, check_claims(email_body, research_json), judge(email_body, research_json))
