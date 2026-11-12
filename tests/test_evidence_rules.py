"""Offline tests for the evidence-map rules and the scoring fix.
Run from the repo root:  PYTHONPATH=. python3 tests/test_evidence_rules.py
No network, no LLM: scraper.fetch and extract_profile are stubbed."""
import qualify
import scraper
from qualify import certs_held, missing_facts, qualify as run_qualify, score
from schemas import CompanyProfile


def page(url, mentioned=(), links=()):
    return {"url": url, "ok": True, "text": "x" * 200, "certs": {"mentioned": list(mentioned), "in_progress": False},
            "links": [{"url": u, "text": t} for u, t in links], "method": "request", "error": None}


HOME_LINKS = [("https://acme.io/careers", "Careers"), ("https://acme.io/pricing", "Pricing"),
              ("https://acme.io/security", "Security"), ("https://acme.io/about", "About")]


def stub_site(home_mentions=(), profile=None):
    """Patch fetch/extract so run_qualify sees a fixed site."""
    scraper.fetch = lambda url: page(url if url.endswith("/") else url, home_mentions if url.endswith(".io") or url.endswith("io/") else (), HOME_LINKS)
    qualify.scraper.fetch = scraper.fetch
    qualify.extract_profile = lambda domain, pages: profile or CompanyProfile()


def test_headcount_alone_does_not_answer_stage():
    p = CompanyProfile(employee_estimate=80, is_b2b_saas=True, buyer_type="CTOs")
    pages = [page("https://acme.io/", mentioned=["SOC 2"])]
    assert "stage" in missing_facts(p, pages)


def test_regex_badge_counts_as_claim_in_score():
    p = CompanyProfile(is_b2b_saas=True, buyer_type="CTOs", funding_stage="Series B")
    pages = [page("https://acme.io/", mentioned=["SOC 2"])]     # model said nothing, badge found
    assert certs_held(p, pages) == {"SOC 2"}
    pts, reasons = score(p, pages)
    assert any("holds SOC 2 only" in r for r in reasons), reasons
    assert not any("no SOC 2" in r for r in reasons)


def test_score_is_the_same_whether_or_not_model_copied_the_badge():
    pages = [page("https://acme.io/", mentioned=["SOC 2"])]
    with_model = CompanyProfile(is_b2b_saas=True, buyer_type="x", funding_stage="Series B",
                                certifications_claimed=["SOC 2"])
    without_model = CompanyProfile(is_b2b_saas=True, buyer_type="x", funding_stage="Series B")
    assert score(with_model, pages)[0] == score(without_model, pages)[0]


def test_unanswered_certs_flags_the_company():
    stub_site(home_mentions=())
    qualify.extract_profile = lambda d, pages: CompanyProfile(
        is_b2b_saas=True, buyer_type="CTOs", funding_stage="Series B", has_enterprise_tier=True,
        named_customers=["A", "B"], handles_sensitive_data=True)
    res = run_qualify("acme.io", lambda m: None)
    assert res["decision"] == "QUALIFIED_FLAGGED", res["decision"]
    assert any("certification" in f for f in res["flags"])


def test_unknown_stage_is_flagged_not_dropped():
    stub_site(home_mentions=["ISO 27001"])
    qualify.extract_profile = lambda d, pages: CompanyProfile(is_b2b_saas=True, buyer_type="CTOs")
    res = run_qualify("acme.io", lambda m: None)
    assert res["decision"] == "QUALIFIED_FLAGGED" and any("stage" in f for f in res["flags"]), res


def test_both_certs_held_disqualifies():
    stub_site(home_mentions=["SOC 2", "ISO 27001"])
    qualify.extract_profile = lambda d, pages: CompanyProfile(is_b2b_saas=True, buyer_type="CTOs", funding_stage="Series A")
    res = run_qualify("acme.io", lambda m: None)
    assert res["decision"] == "DISQUALIFIED" and "both SOC 2 and ISO 27001" in res["reason"], res["reason"]


def test_per_question_fetch_limits_are_respected():
    stub_site(home_mentions=())
    qualify.extract_profile = lambda d, pages: CompanyProfile()     # nothing ever answered
    res = run_qualify("acme.io", lambda m: None)
    counts = {}
    for t in res["trace"]:
        if "for" in t:
            counts[t["for"]] = counts.get(t["for"], 0) + 1
    from config import FETCH_LIMITS, MAX_EXTRA_FETCHES
    assert all(counts.get(q, 0) <= FETCH_LIMITS[q] for q in FETCH_LIMITS), counts
    assert sum(counts.values()) <= MAX_EXTRA_FETCHES, counts


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t(); print("PASS", t.__name__)
        except Exception as e:
            failed += 1; print("FAIL", t.__name__, "-", repr(e))
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
