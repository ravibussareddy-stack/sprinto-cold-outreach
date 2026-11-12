"""Offline tests for the measurement targets and kill criteria.
Run from the repo root:  PYTHONPATH=. python3 tests/test_measure.py"""
import measure


def test_no_reviews_gives_no_verdicts():
    m = measure.compute([])
    assert m["reviewed"] == 0 and m["rewrite_rate"] is None and m["verdicts"] == []


def test_rewrite_rate_only_counts_drafts():
    rows = [("kept", True, 1.5), ("rewritten", True, 3.0), ("not_drafted", None, 2.0)]
    m = measure.compute(rows)
    assert m["drafts"] == 2 and m["rewrite_rate"] == 0.5


def test_kill_when_more_than_half_rewritten():
    rows = [("rewritten", True, 1.0), ("rewritten", True, 1.0), ("kept", True, 1.0)]
    assert any(v.startswith("KILL: more than half") for v in measure.compute(rows)["verdicts"])


def test_on_target_when_under_20_percent_rewritten():
    rows = [("kept", True, 1.0)] * 9 + [("rewritten", True, 1.0)]
    m = measure.compute(rows)
    assert m["rewrite_rate"] == 0.1 and "on target: rewrites under 20%" in m["verdicts"]


def test_poc_kill_below_60_percent():
    rows = [("kept", True, 1.0), ("kept", False, 1.0), ("kept", False, 1.0)]
    assert any(v == "KILL: PoC accuracy under 60%" for v in measure.compute(rows)["verdicts"])


def test_poc_unknown_rows_are_ignored():
    rows = [("kept", None, 1.0), ("kept", True, 1.0)]
    assert measure.compute(rows)["poc_accuracy"] == 1.0


def test_time_target_is_2_minutes():
    assert any("under 2 min" in v for v in measure.compute([("kept", True, 1.0)])["verdicts"])
    assert any("above target" in v for v in measure.compute([("kept", True, 5.0)])["verdicts"])


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
