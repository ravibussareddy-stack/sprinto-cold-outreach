"""Offline tests for the disagreement score (model calls stubbed).
Run from the repo root:  PYTHONPATH=. python3 tests/test_disagreement.py"""
import disagreement as d
from disagreement import Claim, ClaimCheck, JudgeVerdict


def claims(*supported):
    return ClaimCheck(claims=[Claim(text=f"c{i}", supported=s) for i, s in enumerate(supported)])


def faithful():
    return JudgeVerdict(faithful=True, unsupported=[], reason="ok")


def unfaithful():
    return JudgeVerdict(faithful=False, unsupported=["invented customer"], reason="invented")


def test_clean_draft_scores_zero():
    r = d.score([], claims(True, True), faithful())
    assert r["score"] == 0 and r["parts"] == {"rules": 0, "claims": 0, "judge": 0}


def test_each_rule_failure_counts_and_caps_at_two():
    assert d.score(["len"], claims(True), faithful())["parts"]["rules"] == 15
    assert d.score(["a", "b", "c"], claims(True), faithful())["parts"]["rules"] == 30


def test_unsupported_claims_scale_the_claim_part():
    r = d.score([], claims(True, False, False, True), faithful())
    assert r["parts"]["claims"] == 20 and r["claims_unsupported"] == ["c1", "c2"]


def test_unfaithful_judge_adds_full_weight():
    assert d.score([], claims(True), unfaithful())["parts"]["judge"] == 30


def test_score_is_capped_at_100_and_bounded():
    r = d.score(["a", "b", "c"], claims(False, False), unfaithful())
    assert r["score"] == 100 and 0 <= r["score"] <= 100


def test_report_only_in_build_1():
    assert d.HOLD_THRESHOLD is None
    r = d.score(["a", "b"], claims(False), unfaithful())
    assert r["held"] is False


def test_assess_uses_both_model_calls():
    calls = []
    real_claims, real_judge = d.check_claims, d.judge
    d.check_claims = lambda body, res: (calls.append("claims"), claims(True, False))[1]
    d.judge = lambda body, res: (calls.append("judge"), unfaithful())[1]
    try:
        r = d.assess("Hi X, ...", "{}", [])
    finally:
        d.check_claims, d.judge = real_claims, real_judge
    assert calls == ["claims", "judge"] and r["score"] == 20 + 30


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
