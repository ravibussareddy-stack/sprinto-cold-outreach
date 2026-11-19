"""Certification evidence: who a mention is about, the three states, and the PostHog regression.
No network, no LLM: fetch and extract_profile are stubbed. The PostHog excerpt is a dated copy of
its public security page (tests/fixtures/posthog_security_2026-11-19.txt)."""
import os

import qualify
import scraper
from schemas import CompanyProfile

FIXTURE = open(os.path.join(os.path.dirname(__file__), "fixtures", "posthog_security_2026-11-19.txt")).read()
PROFILE = dict(is_b2b_saas=True, buyer_type="Engineering teams", funding_stage="Series B",
               has_enterprise_tier=True, named_customers=["A", "B"], handles_sensitive_data=True)


def page(url, raw="", ok=True, links=()):
    return {"url": url, "ok": ok, "error": None if ok else "404", "method": "request", "text": "x " * 100,
            "raw_text": raw, "certs": scraper.detect_certifications(raw), "links": [{"url": u, "text": t} for u, t in links]}


def run(site: dict, profile=None):
    """site: url -> raw text, or None for a page that does not exist."""
    def fetch(url):
        for u, raw in site.items():
            if url.rstrip("/") == u.rstrip("/"):
                return page(u, raw or "", ok=raw is not None, links=[])
        return page(url, "", ok=False)
    qualify.scraper.fetch = fetch
    qualify.extract_profile = lambda d, pages: CompanyProfile(**(profile or PROFILE))
    return qualify.qualify("acme.io", lambda m: None)


# ---- who a mention is about
def test_mentions_about_customers_or_vendors_are_not_claims():
    assert scraper.detect_certifications("We help startups get SOC 2 compliant in weeks.")["self_claimed"] == []
    assert scraper.detect_certifications("Our infrastructure runs on AWS, which is SOC 2 audited.")["self_claimed"] == []
    assert scraper.detect_certifications("We are SOC 2 Type II certified.")["self_claimed"] == ["SOC 2"]


def test_integration_lists_are_not_tools_in_use():
    assert scraper.detect_certifications("Integrates with Vanta and Drata for compliance")["tools"] == []


# ---- PostHog regression: security page holds the SOC 2 claim; homepage says nothing
def test_posthog_goes_dormant_once_its_security_page_is_read():
    res = run({"https://acme.io": "Product analytics for engineers", "https://acme.io/security": FIXTURE})
    assert res["decision"] == "DORMANT", res["decision"]
    c = res["certification"]
    assert c["status"] == "confirmed" and "SOC 2" in c["held"]
    assert [t["tool"] for t in c["compliance_tools"]] == ["Drata"]
    assert any(e["who"] == "self" and e["url"].endswith("/security") for e in c["evidence"])
    assert res["trace"][1]["for"] == "certs"                  # the first extra fetch goes to certification


# ---- three states
def test_unknown_status_withholds_greenfield_points_and_flags():
    res = run({"https://acme.io": "Product analytics for engineers"})     # no security / trust page anywhere
    assert res["certification"]["status"] == "unknown"
    assert not any("greenfield)" in r for r in res["score_reasons"])
    assert any("greenfield points are withheld" in r for r in res["score_reasons"])
    assert res["decision"] == "QUALIFIED_FLAGGED" and any("unknown" in f for f in res["flags"])


def test_none_found_on_a_security_page_earns_greenfield_points():
    res = run({"https://acme.io": "Product analytics", "https://acme.io/security": "We encrypt data at rest and use SSO."})
    assert res["certification"]["status"] == "none_found"
    assert any("+20 no SOC 2 / ISO 27001 claimed on https://acme.io/security" in r for r in res["score_reasons"])


def test_in_progress_is_its_own_state():
    res = run({"https://acme.io": "Analytics", "https://acme.io/security": "Our SOC 2 audit is in progress."})
    assert res["certification"]["status"] == "in_progress" and res["decision"] != "DORMANT"


def test_compliance_software_seller_is_not_dormant():
    res = run({"https://acme.io": "We help startups get SOC 2 and ISO 27001 compliant, fast.",
               "https://acme.io/security": "We encrypt customer data and run yearly pen tests."})
    assert res["decision"] != "DORMANT" and res["certification"]["held"] == []


def test_a_compliance_tool_in_use_withholds_greenfield_points():
    res = run({"https://acme.io": "Analytics", "https://acme.io/security": "Our trust center is powered by Vanta."})
    assert not any(r.startswith("+20") for r in res["score_reasons"])
    assert any("Vanta" in f for f in res["flags"])


def test_a_non_compliance_standard_is_not_a_certification():
    res = run({"https://acme.io": "Passkeys for developers", "https://acme.io/security": "We encrypt data."},
              profile=PROFILE | {"certifications_claimed": ["FIDO2"]})
    assert res["decision"] != "DORMANT" and res["certification"]["held"] == []


def test_evidence_quotes_the_plainest_statement_of_the_claim():
    ev = scraper.detect_certifications(FIXTURE)["evidence"]
    assert any(e["who"] == "self" and e["sentence"].startswith("PostHog is certified as SOC 2 Type 2") for e in ev), ev
