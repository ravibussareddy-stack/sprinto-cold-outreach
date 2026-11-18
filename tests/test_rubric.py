"""The editable rubric: bad edits fall back to defaults, and the filters follow it."""
import rubric
from qualify import score, short_circuit
from schemas import CompanyProfile


def page(mentioned=()):
    return {"url": "https://acme.io/", "ok": True, "text": "x" * 200,
            "certs": {"mentioned": list(mentioned), "in_progress": False}, "links": [], "method": "request", "error": None}


SAAS = dict(is_b2b_saas=True, buyer_type="CTOs", funding_stage="Series B")


def teardown_function(_):
    rubric.use(rubric.defaults())


def test_bad_values_and_unknown_keys_are_ignored():
    r = rubric.merge(rubric.defaults(), {"decision": {"qualify_score": -5, "made_up": 1},
                                          "stage_points": {"seed": "lots"}, "nonsense": {"x": 1}})
    d = rubric.defaults()
    assert r["decision"]["qualify_score"] == d["decision"]["qualify_score"]
    assert r["stage_points"]["seed"] == d["stage_points"]["seed"] and "nonsense" not in r


def test_inconsistent_thresholds_fall_back_to_defaults():
    r = rubric.merge(rubric.defaults(), {"decision": {"qualify_score": 30, "review_score": 60}})
    assert r["decision"] == rubric.defaults()["decision"]


def test_unknown_dormant_rule_falls_back():
    assert rubric.merge(rubric.defaults(), {"certifications": {"dormant_rule": "maybe"}})["certifications"]["dormant_rule"] == "any_named_cert"


def test_dormant_rule_any_named_cert_is_the_default():
    p = CompanyProfile(**SAAS)
    assert short_circuit(p, [page(["SOC 2"])])[0] == "DORMANT"
    assert short_circuit(p, [page(["GDPR"])]) is None          # GDPR is not a certification


def test_dormant_rule_soc2_and_iso_lets_soc2_only_through_as_expansion():
    rubric.use({"certifications": {"dormant_rule": "soc2_and_iso"}})
    p = CompanyProfile(**SAAS)
    assert short_circuit(p, [page(["SOC 2"])]) is None
    pts, why = score(p, [page(["SOC 2"])])
    assert any("holds SOC 2 only" in w for w in why), why
    assert short_circuit(p, [page(["SOC 2", "ISO 27001"])])[0] == "DORMANT"


def test_dormant_rule_off_never_short_circuits_on_certs():
    rubric.use({"certifications": {"dormant_rule": "off"}})
    assert short_circuit(CompanyProfile(**SAAS), [page(["SOC 2", "ISO 27001"])]) is None


def test_points_follow_the_rubric():
    p = CompanyProfile(**SAAS)
    base, _ = score(p, [page()])
    rubric.use({"stage_points": {"series_a_c": 40}})
    more, why = score(p, [page()])
    assert more == base + 10 and any(w.startswith("+40 stage") for w in why)


def test_excluded_certifications_are_editable():
    rubric.use({"certifications": {"not_certifications": ["gdpr", "hipaa"]}})
    assert short_circuit(CompanyProfile(**SAAS), [page(["HIPAA"])]) is None
