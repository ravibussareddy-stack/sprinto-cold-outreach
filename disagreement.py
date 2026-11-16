"""Disagreement score for a drafted email: how far the draft is from 'intact'.

Build 1 (now): computed and reported only. Nothing is held back.
Build 2 (later): drafts at or above HOLD_THRESHOLD are held for a human. The threshold
is set from the Growth Lead's review data (review.py), not guessed.

Three independent signals, each 0..1, weighted into a 0..100 score (higher = more disagreement):
  rules   (30): deterministic draft checks (name, length, placeholders, CTA). Capped at 2 failures.
  claims  (40): share of the email's factual claims the research does NOT support.
                Found by one model call that lists each claim and checks it against the research.
  judge   (30): a separate model call, with a sceptical-reviewer prompt, says whether the email
                is faithful to the research. Unfaithful = full weight.
"""
from pydantic import BaseModel, Field

from llm import ask

WEIGHTS = {"rules": 30, "claims": 40, "judge": 30}
HOLD_THRESHOLD = None          # build 2 sets this from review data; None = report only


class Claim(BaseModel):
    text: str = Field(description="One factual claim made in the email, quoted or paraphrased")
    supported: bool = Field(description="True only if the research explicitly supports this claim")


class ClaimCheck(BaseModel):
    claims: list[Claim] = Field(description="Every factual statement about the company or the recipient")


class JudgeVerdict(BaseModel):
    faithful: bool = Field(description="True only if the email invents nothing and overstates nothing")
    unsupported: list[str] = Field([], description="Statements that go beyond the research")
    reason: str


CLAIM_SYSTEM = """List every factual claim the email makes about the company or the recipient.
For each one, say whether the research explicitly supports it. Generic statements about
what Sprinto does are not claims about the company. Never assume support."""

JUDGE_SYSTEM = """You are a sceptical compliance reviewer checking a cold email before it is sent.
Assume the email is wrong until the research shows otherwise. Flag any invented fact,
overstated fact, or a made-up customer, number, or certification. Be strict."""


def check_claims(email_body: str, research_json: str) -> ClaimCheck:
    user = f"Research (JSON):\n{research_json}\n\nEmail:\n{email_body}"
    return ask(ClaimCheck, CLAIM_SYSTEM, user, temperature=0.0)


def judge(email_body: str, research_json: str) -> JudgeVerdict:
    user = f"Research (JSON):\n{research_json}\n\nEmail:\n{email_body}"
    return ask(JudgeVerdict, JUDGE_SYSTEM, user, temperature=0.0)


def score(rule_failures: list[str], claims: ClaimCheck, verdict: JudgeVerdict) -> dict:
    total = len(claims.claims)
    unsupported_fraction = (sum(1 for c in claims.claims if not c.supported) / total) if total else 0.0
    rules_part = min(len(rule_failures), 2) / 2
    judge_part = 0.0 if verdict.faithful else 1.0
    parts = {
        "rules": round(WEIGHTS["rules"] * rules_part),
        "claims": round(WEIGHTS["claims"] * unsupported_fraction),
        "judge": round(WEIGHTS["judge"] * judge_part),
    }
    total_score = sum(parts.values())
    return {
        "score": min(100, total_score),
        "parts": parts,
        "claims_total": total,
        "claims_unsupported": [c.text for c in claims.claims if not c.supported],
        "judge_faithful": verdict.faithful,
        "judge_reason": verdict.reason,
        "judge_unsupported": verdict.unsupported,
        "rule_failures": rule_failures,
        "held": HOLD_THRESHOLD is not None and total_score >= HOLD_THRESHOLD,
    }


def assess(email_body: str, research_json: str, rule_failures: list[str]) -> dict:
    return score(rule_failures, check_claims(email_body, research_json), judge(email_body, research_json))
