"""Self-reinforcement: learn from every finished conversation.

When a conversation ends (check_replies.py), this:
1. labels the outcome: booked > positive > unclear > negative > non_respondent
2. asks the model for up to 3 specific, reusable lessons from the emails and replies -
   what to repeat when it worked, what to avoid when it did not - and stores them in the
   feedback repo with the outcome's score, so lessons from better outcomes rank higher
3. if the reply was positive, stores the email that earned it as an exemplar for that role

The writer then uses the top-ranked relevant lessons and exemplars for the next drafts.
"""
from typing import Literal

from pydantic import BaseModel, Field

import db
import feedback
from llm import ask

OUTCOME_SCORES = {"booked": 1.0, "positive": 0.8, "unclear": 0.4, "negative": 0.3, "non_respondent": 0.1}


class Lesson(BaseModel):
    guidance: str = Field(description="One imperative sentence for writing future emails, specific "
                                      "(hook type, angle, length, ask, tone or timing), never generic")
    scope: Literal["global", "role", "industry"] = Field(
        description="'role' if it depends on the recipient's role, 'industry' if on their industry, else 'global'")
    why: str = Field(description="The evidence in this conversation, in one sentence")


class Lessons(BaseModel):
    lessons: list[Lesson] = Field(description="Up to 3 lessons, most useful first")


SYSTEM = """You analyse one finished cold-email conversation from Sprinto (compliance automation)
and its outcome, to improve future emails.
- If the outcome was good (booked or positive), say what to repeat: the hook type, the angle,
  the length, the ask, the follow-up that worked.
- If it was bad (negative or no reply), say what to avoid or change next time.
Be specific and reusable across companies. Do not restate a lesson that already exists (given).
Never include the company's or person's name in a lesson."""


def outcome_label(outcome: str | None, sentiment: str | None) -> str:
    if outcome == "booked":
        return "booked"
    if outcome == "not_interested" or sentiment == "negative":
        return "negative"
    if outcome in ("non_respondent", "no_reply"):
        return "non_respondent"
    if sentiment == "positive":
        return "positive"
    return "unclear"


def _scope_value(kind: str, title: str | None, industries: list[str] | None) -> str:
    if kind == "role" and feedback.role_of(title):
        return f"role:{feedback.role_of(title)}"
    if kind == "industry" and industries:
        return f"industry:{industries[0].strip().lower()}"
    return "global"


def from_thread(thread_id: str, domain: str, ctx: dict, outcome: str | None, sentiment: str | None,
                log=print) -> dict:
    label = outcome_label(outcome, sentiment)
    score = OUTCOME_SCORES[label]
    title, industries = ctx.get("title"), ctx.get("industries") or []
    emails = db.thread_emails(thread_id) or []
    if not emails:
        return {"label": label, "lessons": []}
    convo = "\n\n".join(f"[{'PROSPECT REPLY' if e['kind'] == 'reply' else 'WE SENT: ' + e['kind']}]\n{e['body']}"
                        for e in emails)
    existing = feedback.relevant(title, industries, limit=15)
    user = (f"Recipient role: {title or 'unknown'}; industries: {', '.join(industries) or 'unknown'}\n"
            f"Outcome: {label}\n\nConversation, oldest first:\n{convo}\n\nExisting lessons:\n"
            + ("\n".join(f"- {g['guidance']}" for g in existing) or "(none)"))
    lessons = ask(Lessons, SYSTEM, user).lessons[:3]
    saved = []
    for l in lessons:
        scope = _scope_value(l.scope, title, industries)
        fid = feedback.add(l.guidance, scope, "outcome", score,
                           {"domain": domain, "thread_id": thread_id, "outcome": label, "why": l.why})
        saved.append({"id": fid, "scope": scope, "guidance": l.guidance})
        log(f"  lesson ({label}, {scope}): {l.guidance}")
    if label in ("booked", "positive"):
        # The email they answered: the last one we sent before their first reply.
        first_reply = next((i for i, e in enumerate(emails) if e["kind"] == "reply"), None)
        sent_before = [e for e in emails[:first_reply] if e["kind"] == "cold" or e["kind"].startswith("followup")]
        if sent_before:
            win = sent_before[-1]
            db.add_exemplar(domain, feedback.role_of(title), win["subject"] or "", win["body"], label, score)
            log(f"  saved the {win['kind']} email as an exemplar ({label})")
    return {"label": label, "score": score, "lessons": saved}
