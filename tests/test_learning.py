"""Quality checks, the feedback repo and learning from outcomes (database and model stubbed)."""
import db
import feedback
import learn
import writer
from schemas import EmailDraft

GOOD = """Good morning Priya,

Hope you're doing well.

Congratulations on launching Ledgerly's Enterprise plan last month. The first large customer usually asks for a SOC 2 report before the second meeting.

For a team your size, that often means engineers spending weeks gathering evidence instead of shipping. Sprinto connects to your cloud and identity tools, collects that evidence automatically and gets companies audit-ready in weeks rather than months.

Would a 15-minute call next week be useful?

Kind regards,
Sarah Mitchell
Sprinto"""


def checks(body, **kw):
    d = EmailDraft(subject=kw.get("subject", "Ledgerly's first enterprise SOC 2 review"), body=body, hook_used="x", reasoning="x")
    return {c["check"]: c["ok"] for c in writer.quality_checks(
        d, "Priya", kw.get("greeting", "Good morning Priya,"), "Sarah Mitchell, Sprinto", "Ledgerly")}


# ---- quality checks ----
def test_well_structured_email_passes_every_check():
    assert all(checks(GOOD).values()), checks(GOOD)


def test_structure_problems_are_named():
    bad = GOOD.replace("Hope you're doing well.\n\n", "").replace("Kind regards,", "Cheers then,") \
              .replace("instead of shipping.", "instead of shipping. Ready to optimize?")
    r = checks(bad)
    assert not r["wellbeing_line"] and not r["sign_off"] and not r["one_question"] and not r["uk_spelling"]
    assert r["greeting"]


def test_subject_must_be_capitalised_and_name_the_company():
    r = checks(GOOD, subject="security questionnaires for enterprise deals")
    assert not r["subject_capitalised"] and not r["subject_names_company"]
    assert writer.fix_subject("security reviews at Ledgerly") == "Security reviews at Ledgerly"


def test_feedback_sign_off_is_used_and_accepted():
    fb = [{"guidance": 'It needs "best regards" instead of "kind regards".', "scope": "global", "outcome_score": 1.0}]
    assert writer.preferred_sign_off(fb) == "Best regards,"
    assert writer.preferred_sign_off([]) == "Kind regards,"
    assert checks(GOOD.replace("Kind regards,", "Best regards,"))["sign_off"]
    assert "OVERRIDE the default style rules" in writer.instructions_block(fb)


def test_wrong_time_of_day_greeting_fails():
    assert not checks(GOOD, greeting="Good evening Priya,")["greeting"]


# ---- feedback repo ----
def test_roles_and_scopes():
    assert feedback.role_of("Head of Security & Compliance") == "security"
    assert feedback.role_of("Co-founder & CTO") == "cto"
    assert feedback.role_of("Co-founder & CEO") == "ceo"
    assert feedback.scopes_for("CTO", ["Fintech", "Banking"]) == ["global", "role:cto", "industry:fintech", "industry:banking"]


def test_guidance_is_given_ranked_with_the_conflict_rule():
    block = writer.instructions_block([{"outcome_score": 1.0, "scope": "role:cto", "guidance": "Lead with a named customer"},
                                    {"outcome_score": 0.3, "scope": "global", "guidance": "Lead with an award"}])
    assert block.index("named customer") < block.index("award")
    assert "if two conflict, follow the higher-ranked one" in block.lower()


# ---- learning from outcomes ----
def test_outcome_labels():
    assert learn.outcome_label("booked", "positive") == "booked"
    assert learn.outcome_label("not_interested", "negative") == "negative"
    assert learn.outcome_label("non_respondent", None) == "non_respondent"
    assert learn.outcome_label("escalated_to_growth_lead", "positive") == "positive"


def _stub(monkeypatch, emails):
    saved = {"feedback": [], "exemplars": []}
    monkeypatch.setattr(db, "thread_emails", lambda tid: emails)
    monkeypatch.setattr(db, "feedback_for", lambda scopes, limit: [])
    monkeypatch.setattr(db, "add_feedback", lambda *a: saved["feedback"].append(a) or len(saved["feedback"]))
    monkeypatch.setattr(db, "add_exemplar", lambda *a: saved["exemplars"].append(a))
    monkeypatch.setattr(learn, "ask", lambda schema, system, user, **k: learn.Lessons(lessons=[
        learn.Lesson(guidance="Lead with a named enterprise customer", scope="role", why="they replied to it")]))
    return saved


CONVO = [{"kind": "cold", "subject": "s1", "body": "cold email"},
         {"kind": "followup_1", "subject": "Re: s1", "body": "follow-up one"},
         {"kind": "reply", "subject": "Re: s1", "body": "yes, keen"},
         {"kind": "proposal", "subject": "Re: s1", "body": "times"}]


def test_booked_conversation_adds_scored_lessons_and_the_winning_email(monkeypatch):
    saved = _stub(monkeypatch, CONVO)
    r = learn.from_thread("t1", "acme.io", {"title": "CTO", "industries": ["Fintech"]}, "booked", "positive", log=lambda m: None)
    assert r["label"] == "booked" and r["score"] == 1.0
    source, scope, text, score, evidence = saved["feedback"][0]
    assert (source, scope, score) == ("outcome", "role:cto", 1.0) and evidence["outcome"] == "booked"
    # the follow-up was the email they answered
    assert saved["exemplars"] == [("acme.io", "cto", "Re: s1", "follow-up one", "booked", 1.0)]


def test_negative_conversation_adds_low_scored_lessons_and_no_exemplar(monkeypatch):
    saved = _stub(monkeypatch, CONVO[:3])
    r = learn.from_thread("t2", "acme.io", {"title": "CEO"}, "not_interested", "negative", log=lambda m: None)
    assert r["label"] == "negative" and saved["feedback"][0][3] == 0.3 and saved["exemplars"] == []


def test_sign_off_always_ends_with_the_exact_sender_lines():
    body = "Good morning Priya,\n\nHope you're doing well.\n\nText?\n\nBest regards,\nRavi Bussareddy"
    out = writer.fix_sign_off(body, "Ravi Bussareddy, Sprinto")
    assert out.endswith("Best regards,\nRavi Bussareddy\nSprinto")
    assert writer.fix_sign_off(out, "Ravi Bussareddy, Sprinto") == out          # idempotent
