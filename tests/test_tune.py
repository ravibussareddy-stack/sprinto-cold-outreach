"""Self-reinforcing rubric: what the tuner changes, by how much, and why (no database)."""
import rubric
import tune


def setup_function(_):
    tune._recently_overridden = lambda section, key: False


def teardown_function(_):
    rubric.use(rubric.defaults())


def row(positive, score=60, signals=()):
    return {"positive": positive, "replied": positive, "booked": False, "score": score, "signals": set(signals)}


NO_REVIEWS = {"above": [], "below": []}


def test_not_enough_data_changes_nothing_and_says_why():
    changes, notes = tune.propose(rubric.load(), [row(True)] * 3, NO_REVIEWS)
    assert changes == [] and any("3 of 8" in n for n in notes)


def test_a_signal_that_predicts_replies_gains_points_with_a_plain_reason():
    rows = [row(True, signals={"named_customers"})] * 5 + [row(False)] * 5
    changes, _ = tune.propose(rubric.load(), rows, NO_REVIEWS)
    ch = next(c for c in changes if c["key"] == "named_customers")
    assert (ch["old"], ch["new"]) == (10, 15)                      # one step of max_step (5)
    assert "100%" in ch["reason"] and "0%" in ch["reason"] and "counts for more" in ch["reason"]


def test_a_signal_that_predicts_silence_loses_points_but_never_below_zero():
    rubric.use({"compliance_points": {"recent_trigger": 3}})
    rows = [row(False, signals={"recent_trigger"})] * 5 + [row(True)] * 5
    changes, _ = tune.propose(rubric.load(), rows, NO_REVIEWS)
    ch = next(c for c in changes if c["key"] == "recent_trigger")
    assert ch["new"] == 0 and "counts for less" in ch["reason"]


def test_small_differences_are_treated_as_noise():
    rows = [row(True, signals={"named_customers"})] * 3 + [row(False, signals={"named_customers"})] * 2 + \
           [row(True)] * 3 + [row(False)] * 2
    changes, _ = tune.propose(rubric.load(), rows, NO_REVIEWS)
    assert not any(c["key"] == "named_customers" for c in changes)


def test_flagged_companies_doing_as_well_lowers_the_qualify_score():
    rows = [row(True, score=60)] * 2 + [row(False, score=60)] * 2 + [row(True, score=40)] * 3 + [row(False, score=40)] * 1
    changes, _ = tune.propose(rubric.load(), rows, NO_REVIEWS)
    ch = next(c for c in changes if c["key"] == "qualify_score")
    assert (ch["old"], ch["new"]) == (50, 45) and "bar was too high" in ch["reason"]


def test_mostly_kept_reviews_raise_the_review_threshold():
    reviews = {"above": ["kept"] * 7 + ["rewritten"], "below": []}
    changes, _ = tune.propose(rubric.load(), [], reviews)
    ch = next(c for c in changes if c["key"] == "disagreement_threshold")
    assert (ch["old"], ch["new"]) == (20, 25) and "fewer good drafts are held up" in ch["reason"]


def test_rewrites_below_threshold_lower_it():
    reviews = {"above": [], "below": ["rewritten"] * 4 + ["kept"] * 4}
    changes, _ = tune.propose(rubric.load(), [], reviews)
    ch = next(c for c in changes if c["key"] == "disagreement_threshold")
    assert ch["new"] == 15 and "slipping through" in ch["reason"]


def test_settings_you_changed_recently_are_left_alone():
    tune._recently_overridden = lambda section, key: key == "named_customers"
    rows = [row(True, signals={"named_customers"})] * 5 + [row(False)] * 5
    changes, notes = tune.propose(rubric.load(), rows, NO_REVIEWS)
    assert not any(c["key"] == "named_customers" for c in changes)
    assert any("left alone" in n for n in notes)


def test_bigger_steps_follow_the_rubric():
    rubric.use({"auto_tune": {"max_step": 10}})
    rows = [row(True, signals={"named_customers"})] * 5 + [row(False)] * 5
    changes, _ = tune.propose(rubric.load(), rows, NO_REVIEWS)
    assert next(c for c in changes if c["key"] == "named_customers")["new"] == 20
