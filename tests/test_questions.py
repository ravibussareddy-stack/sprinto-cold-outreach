"""Judgement questions: situations the rules leave to you, their defaults, and what your ruling changes."""
import pytest

import qualify
import questions
import scraper
from schemas import CompanyProfile, UnusualFinding

PROFILE = dict(is_b2b_saas=True, buyer_type="Teams", funding_stage="Series B", has_enterprise_tier=True,
               named_customers=["A", "B"], handles_sensitive_data=True, one_line_description="Analytics for teams")


@pytest.fixture(autouse=True)
def no_rulings():
    questions._cache = {}
    yield
    questions._cache = {}


def run(monkeypatch, security_text, profile=None, rulings=None):
    questions._cache = dict(rulings or {})
    site = {"https://acme.io": "Analytics for teams", "https://acme.io/security": security_text}
    def fetch(u):
        raw = site.get(u.rstrip("/"))
        return {"url": u.rstrip("/"), "ok": raw is not None, "error": None if raw is not None else "http_404",
                "method": "request", "text": "x " * 100, "raw_text": raw or "", "certs": scraper.detect_certifications(raw or ""), "links": []}
    monkeypatch.setattr(qualify.scraper, "fetch", fetch)
    monkeypatch.setattr(qualify, "extract_profile", lambda d, pages: CompanyProfile(**(profile or PROFILE)))
    return qualify.qualify("acme.io", lambda m: None)


def topics(res):
    return [f["topic"] for f in res.get("judgement", [])]


def test_type1_only_defaults_to_certified_and_is_raised(monkeypatch):
    res = run(monkeypatch, "We completed our SOC 2 Type I audit and are SOC 2 compliant.")
    assert res["decision"] == "DORMANT" and "soc2_type1_only" in topics(res)
    assert any("judgement call waiting" in f for f in res["flags"])


def test_ruling_type1_is_a_prospect(monkeypatch):
    res = run(monkeypatch, "We completed our SOC 2 Type I audit and are SOC 2 compliant.", rulings={"soc2_type1_only": "prospect"})
    assert res["decision"] != "DORMANT"
    assert not any("judgement call waiting" in f for f in res.get("flags", []))


def test_type2_is_not_a_type1_situation(monkeypatch):
    res = run(monkeypatch, "We are SOC 2 Type II certified.")
    assert "soc2_type1_only" not in topics(res)


def test_badge_only_ruling(monkeypatch):
    assert run(monkeypatch, "Security. SOC 2")["decision"] == "DORMANT"                              # default: badges count
    res = run(monkeypatch, "Security. SOC 2", rulings={"badge_only": "clear_only"})
    assert res["decision"] != "DORMANT"


def test_uses_platform_ruling(monkeypatch):
    text = "Our trust center is powered by Vanta."
    assert run(monkeypatch, text)["decision"] != "DISQUALIFIED"                                     # default: switch prospect
    res = run(monkeypatch, text, rulings={"uses_platform": "skip"})
    assert res["decision"] == "DISQUALIFIED" and "you ruled" in res["reason"]


def test_acquired_ruling(monkeypatch):
    p = PROFILE | {"acquired_by": "BigCo"}
    assert run(monkeypatch, "We encrypt data.", profile=p)["decision"] == "DISQUALIFIED"               # default: skip
    assert run(monkeypatch, "We encrypt data.", profile=p, rulings={"acquired": "prospect"})["decision"] != "DISQUALIFIED"


def test_sells_compliance_ruling(monkeypatch):
    p = PROFILE | {"one_line_description": "Compliance automation for fintech startups"}
    res = run(monkeypatch, "We encrypt data.", profile=p)
    assert "sells_compliance" in topics(res) and res["decision"] != "DISQUALIFIED"                      # default: prospect
    assert run(monkeypatch, "We encrypt data.", profile=p, rulings={"sells_compliance": "skip"})["decision"] == "DISQUALIFIED"


def test_old_report_ruling(monkeypatch):
    text = "We are SOC 2 Type II certified (report dated 2021)."
    assert run(monkeypatch, text)["decision"] == "DORMANT"
    assert run(monkeypatch, text, rulings={"old_report": "lapsed"})["decision"] != "DORMANT"


def test_a_new_situation_is_raised_once_with_a_free_text_question(monkeypatch):
    p = PROFILE | {"unusual_finding": UnusualFinding(topic="certified via parent", finding="SOC 2 held by the parent company",
                                                     question="Should a parent's SOC 2 count for its subsidiary?")}
    res = run(monkeypatch, "We encrypt data.", profile=p)
    assert "new:certified_via_parent" in topics(res)


def test_no_situation_no_question(monkeypatch):
    assert topics(run(monkeypatch, "We are SOC 2 Type II certified.")) == []


def test_a_standard_version_is_not_a_report_date():
    ev = [{"cert": "ISO 27001", "who": "self", "sentence": "Kylas is ISO 27001:2022 certified", "url": "u"}]
    assert questions.old_report(ev) is None
    ev = [{"cert": "SOC 2", "who": "self", "sentence": "Download our 2021 SOC 2 report", "url": "u"}]
    assert questions.old_report(ev)



def test_unknown_certification_gets_a_discovery_email_not_a_hold(monkeypatch):
    questions._cache = {}
    monkeypatch.setattr(qualify.scraper, "fetch", lambda u: {"url": u.rstrip("/"), "ok": u.rstrip("/") == "https://acme.io", "error": None,
        "method": "request", "text": "x " * 100, "raw_text": "", "certs": scraper.detect_certifications(""), "links": []})
    monkeypatch.setattr(qualify, "extract_profile", lambda d, pages: CompanyProfile(is_b2b_saas=True, buyer_type="Teams"))
    res = qualify.qualify("acme.io", lambda m: None)
    assert res["certification"]["status"] == "unknown"
    assert res["decision"] == "QUALIFIED_FLAGGED" and "discovery email" in res["reason"], (res["decision"], res["reason"])
    assert res["profile"].certification_status == "unknown"


def test_the_deck_is_a_valid_pdf_with_the_portfolio():
    import deck
    pdf = deck.build(["SOC 2", "ISO 27001"], [{"name": "ISO/IEC 42001", "who": "AI products", "pitch": "AI governance"}], "Acme")
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert b"ISO/IEC 42001" in pdf and b"not an official Sprinto document" in pdf and b"Prepared for Acme" in pdf
