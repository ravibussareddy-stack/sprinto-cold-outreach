"""Offline tests for the disagreement score (model calls stubbed)."""
import disagreement as d
from disagreement import Claim, ClaimCheck, JudgeVerdict


def claims(*supported, about="company"):
    return ClaimCheck(claims=[Claim(text=f"c{i}", about=about, supported=s) for i, s in enumerate(supported)])


def faithful():
    return JudgeVerdict(faithful=True, unsupported=[], reason="ok")


def unfaithful():
    return JudgeVerdict(faithful=False, unsupported=["invented customer"], reason="invented")


def test_clean_draft_scores_zero():
    r = d.score([], claims(True, True), faithful())
    assert r["score"] == 0 and r["parts"] == {"structure": 0, "wrong_info": 0, "judge": 0}


def test_structure_failures_scale_and_cap_at_three():
    assert d.score(["greeting"], claims(True), faithful())["parts"]["structure"] == 8
    assert d.score(["a", "b", "c", "d"], claims(True), faithful())["parts"]["structure"] == 25


def test_wrong_info_scales_with_unsupported_share():
    r = d.score([], claims(True, False, False, True), faithful())
    assert r["parts"]["wrong_info"] == 22 and r["claims_unsupported"] == ["c1", "c2"]


def test_wrong_sprinto_claims_are_reported_separately():
    c = ClaimCheck(claims=[Claim(text="weeks to hours", about="sprinto", supported=False),
                           Claim(text="Nasdaq is a customer", about="company", supported=True)])
    r = d.score([], c, faithful())
    assert r["wrong_about_sprinto"] == ["weeks to hours"] and r["wrong_about_company"] == []
    assert r["parts"]["wrong_info"] == 22


def test_unfaithful_judge_adds_full_weight():
    assert d.score([], claims(True), unfaithful())["parts"]["judge"] == 30


def test_score_is_capped_and_bounded():
    r = d.score(["a", "b", "c"], claims(False, False), unfaithful())
    assert r["score"] == 100


def test_report_only_in_build_1():
    assert d.HOLD_THRESHOLD is None
    assert d.score(["a", "b"], claims(False), unfaithful())["held"] is False


def test_assess_uses_both_model_calls():
    calls = []
    real_claims, real_judge = d.check_claims, d.judge
    d.check_claims = lambda body, res: (calls.append("claims"), claims(True, False))[1]
    d.judge = lambda body, res: (calls.append("judge"), unfaithful())[1]
    try:
        r = d.assess("Hi X, ...", "{}", [])
    finally:
        d.check_claims, d.judge = real_claims, real_judge
    assert calls == ["claims", "judge"] and r["score"] == 22 + 30
