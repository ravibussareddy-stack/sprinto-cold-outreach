"""Questions: your answers change the next run the way the question promised."""
import qualify
import questions
import scraper
from schemas import CompanyProfile

PROFILE = dict(is_b2b_saas=True, buyer_type="Teams", funding_stage="Series B", has_enterprise_tier=True,
               named_customers=["A", "B"], handles_sensitive_data=True)


def run(monkeypatch, answers, profile=None, site=None):
    site = site or {"https://acme.io": "Analytics for teams"}             # no security page: status unknown
    monkeypatch.setattr(questions, "answer", lambda key: answers.get(key))
    def fetch(u):
        raw = site.get(u.rstrip("/"))
        return {"url": u.rstrip("/"), "ok": raw is not None, "error": None if raw is not None else "http_404",
                "method": "request", "text": "x " * 100, "raw_text": raw or "", "certs": scraper.detect_certifications(raw or ""), "links": []}
    monkeypatch.setattr(qualify.scraper, "fetch", fetch)
    monkeypatch.setattr(qualify, "extract_profile", lambda d, pages: CompanyProfile(**(profile or PROFILE)))
    return qualify.qualify("acme.io", lambda m: None)


def test_without_an_answer_certification_stays_unknown(monkeypatch):
    res = run(monkeypatch, {})
    assert res["certification"]["status"] == "unknown"


def test_answer_neither_earns_the_greenfield_points(monkeypatch):
    res = run(monkeypatch, {"certs:acme.io": "none"})
    assert res["certification"]["status"] == "none_found"
    assert any(r.startswith("+20 no SOC 2 / ISO 27001 (you confirmed neither") for r in res["score_reasons"]), res["score_reasons"]


def test_answer_soc2_makes_it_dormant(monkeypatch):
    res = run(monkeypatch, {"certs:acme.io": "soc2"})
    assert res["decision"] == "DORMANT" and "SOC 2" in res["certs_held"]


def test_answer_not_saas_disqualifies(monkeypatch):
    res = run(monkeypatch, {"saas:acme.io": "no"}, profile=PROFILE | {"is_b2b_saas": None})
    assert res["decision"] == "DISQUALIFIED"


def test_answer_saas_yes_lets_it_be_scored(monkeypatch):
    res = run(monkeypatch, {"saas:acme.io": "yes"}, profile=PROFILE | {"is_b2b_saas": None, "buyer_type": None})
    assert res["decision"] != "MANUAL_REVIEW" or res["score"] is not None


def test_contact_answer_is_used(monkeypatch):
    monkeypatch.setattr(questions, "answer", lambda key: {"first_name": "Priya", "email": "priya@acme.io", "title": "CTO"}
                        if key == "contact:acme.io" else None)
    c = questions.contact_answer("acme.io")
    assert c["first_name"] == "Priya" and c["source"] == "given by you (Questions)"
    monkeypatch.setattr(questions, "answer", lambda key: {"first_name": "", "email": "x"})
    assert questions.contact_answer("acme.io") is None
