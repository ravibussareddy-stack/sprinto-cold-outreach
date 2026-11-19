"""New frameworks: fit rules, and accepted frameworks re-opening certified companies."""
import frameworks
import qualify
import scraper
from schemas import CompanyProfile

AI_CO = dict(is_b2b_saas=True, buyer_type="Data teams", company_name="Acme",
             one_line_description="An AI copilot for data teams", funding_stage="Series B",
             named_customers=["A", "B"], handles_sensitive_data=True, geographic_markets=["US", "Europe"])


def page(url, raw):
    return {"url": url, "ok": True, "error": None, "method": "request", "text": "x " * 100, "raw_text": raw,
            "certs": scraper.detect_certifications(raw), "links": []}


def run(profile, monkeypatch, accepted):
    site = {"https://acme.io": "AI copilot", "https://acme.io/security": "We are SOC 2 Type II certified."}
    monkeypatch.setattr(qualify.scraper, "fetch", lambda u: page(u.rstrip("/"), site.get(u.rstrip("/"), "")) if u.rstrip("/") in site
                        else dict(page(u, ""), ok=False, error="http_404"))
    monkeypatch.setattr(qualify, "extract_profile", lambda d, pages: CompanyProfile(**profile))
    monkeypatch.setattr(frameworks, "accepted", lambda: [frameworks.BY_ID[i] for i in accepted])
    return qualify.qualify("acme.io", lambda m: None)


def test_fit_rules():
    fw = frameworks.BY_ID
    assert frameworks.fit(fw["iso_42001"], AI_CO, ["SOC 2"])
    assert frameworks.fit(fw["iso_42001"], AI_CO, ["ISO 42001"]) is None           # already holds it
    assert frameworks.fit(fw["dora"], AI_CO | {"target_industries": ["Fintech"]}, []) is not None
    assert frameworks.fit(fw["dora"], AI_CO | {"target_industries": ["Fintech"], "geographic_markets": ["US"]}, []) is None
    assert frameworks.fit(fw["cmmc"], AI_CO, []) is None


def test_certified_company_stays_dormant_until_a_fitting_framework_is_accepted(monkeypatch):
    assert run(AI_CO, monkeypatch, [])["decision"] == "DORMANT"
    res = run(AI_CO, monkeypatch, ["iso_42001"])
    assert res["decision"] != "DORMANT", res["decision"]
    assert any(r.startswith("+15 expansion") and "ISO/IEC 42001" in r for r in res["score_reasons"]), res["score_reasons"]
    assert any("expansion prospect" in f for f in res["flags"])
    assert res["expansion"] and res["expansion"][0].startswith("ISO/IEC 42001")


def test_a_framework_that_does_not_fit_leaves_it_dormant(monkeypatch):
    assert run(AI_CO, monkeypatch, ["cmmc"])["decision"] == "DORMANT"
