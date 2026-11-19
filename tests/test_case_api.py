"""The test-case API: input checks and error responses (pipeline stubbed, no network)."""
import case_api
import pipeline

CALLS = []


def test_domain_normalisation():
    assert case_api.normalise_domain("https://www.Atlan.com/about?x=1") == "atlan.com"
    assert case_api.normalise_domain("atlan.com") == "atlan.com"
    for bad in ["", "not a domain", "localhost", "http://", "a.b", 42, None, "exa mple.com"]:
        assert case_api.normalise_domain(bad) is None, bad


def test_missing_and_invalid_inputs_are_400_with_a_code():
    cases = [
        ([], "invalid_body"),
        ({}, "missing_domain"),
        ({"domain": "nope"}, "invalid_domain"),
        ({"domain": "atlan.com", "contact": "Priya"}, "invalid_contact"),
        ({"domain": "atlan.com", "contact": {"email": "p@atlan.com"}}, "invalid_contact"),
        ({"domain": "atlan.com", "contact": {"first_name": "Priya", "email": "not-an-email"}}, "invalid_contact"),
        ({"domain": "atlan.com", "send": "yes"}, "invalid_send"),
        ({"cases": []}, "invalid_cases"),
        ({"cases": [{"domain": "a.com"}] * 4}, "too_many_cases"),
    ]
    for body, code in cases:
        status, out = case_api.run_cases(body)
        assert status == 400 and out["error"]["code"] == code, (body, out)
        assert "input_format" in out


def test_aliases_and_name_split():
    case, err = case_api.parse_case({"website": "https://atlan.com", "poc": {"name": "Priya Sharma", "email": "p@atlan.com"}})
    assert err is None and case["domain"] == "atlan.com"
    assert case["contact"]["first_name"] == "Priya" and case["contact"]["last_name"] == "Sharma"


def _fake(status, **extra):
    def run(domain, do_send, sender, contact=None, fast=False):
        CALLS.append(fast)
        rec = {"domain": domain, "status": status, "seconds": 1.0,
               "qualification": {"decision": extra.get("decision", "QUALIFIED"), "score": 60, "reason": "fit"}}
        if contact:
            rec["poc"] = dict(contact, source="provided with the case")
        rec.update(extra.get("rec", {}))
        return rec
    return run


def test_valid_case_returns_a_full_result(monkeypatch):
    monkeypatch.setattr(pipeline, "process", _fake("DRAFTED", rec={
        "email": {"subject": "s", "body": "Good morning Priya, ...", "hook_used": "h", "reasoning": "r"},
        "pain_points": {"pain_points": [{"title": "t", "why_this_person_feels_it": "w",
                                         "evidence_from_research": "e", "sprinto_angle": "a"}]}}))
    monkeypatch.setattr(case_api.db, "save_run", lambda rec: 99)
    status, out = case_api.run_cases({"domain": "atlan.com", "send": False,
                                      "contact": {"first_name": "Priya", "email": "p@atlan.com", "title": "CTO"}})
    assert status == 200 and out["status"] == "DRAFTED" and out["qualified"] and out["error"] is None
    assert out["email"]["addressed_to"] == "Priya" and out["email"]["sent"] is False
    assert out["pain_points"][0]["evidence"] == "e" and out["run_id"] == 99


def test_pipeline_failures_are_results_not_crashes(monkeypatch):
    monkeypatch.setattr(case_api.db, "save_run", lambda rec: None)
    monkeypatch.setattr(pipeline, "process", _fake("ERROR", rec={"error": "qualify: site unreachable"}))
    status, out = case_api.run_cases({"domain": "unreachable-example.com", "send": False})
    assert status == 200 and out["status"] == "ERROR" and out["error"]["code"] == "pipeline_error"

    def boom(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(pipeline, "process", boom)
    status, out = case_api.run_cases({"domain": "atlan.com", "send": False})
    assert status == 200 and out["status"] == "ERROR" and "unexpected" in out["error"]["message"]


def test_disqualified_and_no_contact_have_reasons(monkeypatch):
    monkeypatch.setattr(case_api.db, "save_run", lambda rec: None)
    monkeypatch.setattr(pipeline, "process", _fake("DORMANT", decision="DORMANT"))
    status, out = case_api.run_cases({"domain": "plane.so", "send": False})
    assert status == 200 and out["qualified"] is False and out["error"] is None and out["company"]["reason"]
    monkeypatch.setattr(pipeline, "process", _fake("NO_POC", rec={"poc": {"error": "no HUNTER_API_KEY and no entry"}}))
    status, out = case_api.run_cases({"domain": "atlan.com", "send": False})
    assert out["error"]["code"] == "no_contact_found" and "HUNTER" in out["error"]["message"]


def test_many_cases_return_a_list(monkeypatch):
    monkeypatch.setattr(case_api.db, "save_run", lambda rec: None)
    monkeypatch.setattr(pipeline, "process", _fake("DORMANT", decision="DORMANT"))
    status, out = case_api.run_cases({"cases": [{"domain": "a-one.com", "send": False}, {"domain": "b-two.com", "send": False}]})
    assert status == 200 and [r["domain"] for r in out["results"]] == ["a-one.com", "b-two.com"]


def test_unreachable_site_has_its_own_code(monkeypatch):
    monkeypatch.setattr(case_api.db, "save_run", lambda rec: None)
    monkeypatch.setattr(pipeline, "process", _fake("FAILED", decision="FAILED",
                                                  rec={"qualification": {"decision": "FAILED", "reason": "homepage unreachable"}}))
    status, out = case_api.run_cases({"domain": "nope-nope-nope.com", "send": False})
    assert status == 200 and out["error"]["code"] == "site_unreachable" and "unreachable" in out["error"]["message"]



def test_mode_defaults_to_fast_and_can_be_full(monkeypatch):
    monkeypatch.setattr(case_api.db, "save_run", lambda rec: None)
    monkeypatch.setattr(pipeline, "process", _fake("DORMANT", decision="DORMANT"))
    CALLS.clear()
    status, out = case_api.run_cases({"domain": "plane.so", "send": False})
    assert status == 200 and CALLS == [True] and out["mode"] == "fast"
    CALLS.clear()
    status, out = case_api.run_cases({"domain": "plane.so", "send": False, "mode": "full"})
    assert CALLS == [False] and out["mode"] == "full"
    status, out = case_api.run_cases({"domain": "plane.so", "mode": "turbo"})
    assert status == 400 and out["error"]["code"] == "invalid_mode"
