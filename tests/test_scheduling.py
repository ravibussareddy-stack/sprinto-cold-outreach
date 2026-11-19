"""Follow-up timers, the learning send window, and the budget pause (no network)."""
import datetime as dt
from zoneinfo import ZoneInfo

import budget
import case_api
import check_replies
import pipeline
import rubric
import sendtime

IST = ZoneInfo("Asia/Kolkata")


def teardown_function(_):
    rubric.use(rubric.defaults())


def test_follow_up_timers_follow_the_rubric():
    assert [check_replies.wait_days(i) for i in range(4)] == [3, 3, 2, 3]
    rubric.use({"followups": {"after_first_days": 4, "after_second_days": 5, "after_third_days": 6, "give_up_after_days": 7}})
    assert [check_replies.wait_days(i) for i in range(4)] == [4, 5, 6, 7]


def test_research_window_until_enough_sends():
    w = sendtime.best_window(rows=[])
    assert w["source"] == "research" and w["days"] == ["tue", "wed", "thu"] and (w["start_hour"], w["end_hour"]) == (8, 11)
    assert sendtime.in_window(dt.datetime(2026, 11, 24, 9, 0, tzinfo=IST), w)          # Tuesday 09:00
    assert not sendtime.in_window(dt.datetime(2026, 11, 24, 15, 0, tzinfo=IST), w)     # Tuesday 15:00
    assert not sendtime.in_window(dt.datetime(2026, 11, 22, 9, 0, tzinfo=IST), w)      # Sunday


def _rows(day_offset, hour, n, replied):
    base = dt.datetime(2026, 11, 2, tzinfo=IST)                                        # a Monday
    return [{"at": base + dt.timedelta(days=day_offset + 7 * i, hours=hour), "replied": i < replied} for i in range(n)]


def test_window_learns_from_observed_replies():
    rubric.use({"send_window": {"min_sends_to_learn": 10}})
    # Mondays 14:00 get replies, Wednesdays 09:00 do not
    rows = _rows(0, 14, 6, 4) + _rows(2, 9, 6, 0)
    w = sendtime.best_window(rows=rows)
    assert w["source"] == "observed" and w["days"][0] == "mon" and w["start_hour"] <= 14 < w["end_hour"], w


def test_fixed_mode_ignores_observations():
    rubric.use({"send_window": {"mode": "fixed", "days": ["mon"], "start_hour": 10, "end_hour": 12, "min_sends_to_learn": 5}})
    w = sendtime.best_window(rows=_rows(2, 9, 20, 20))
    assert w["source"] == "fixed" and w["days"] == ["mon"]


def test_budget_pause_stops_the_pipeline_and_the_api(monkeypatch):
    monkeypatch.setattr(budget, "spent_this_month_inr", lambda: 600.0)
    s = budget.status()
    assert s["paused"] and s["limit_inr"] == 500
    rec = pipeline.process("atlan.com", False, "Sender")
    assert rec["status"] == "PAUSED" and "budget" in rec["error"]
    monkeypatch.setattr(case_api.db, "save_run", lambda rec: None)
    status, out = case_api.run_cases({"domain": "atlan.com", "send": False})
    assert status == 200 and out["error"]["code"] == "budget_exceeded"


def test_zero_budget_means_no_limit(monkeypatch):
    rubric.use({"budget": {"monthly_inr": 0}})
    monkeypatch.setattr(budget, "spent_this_month_inr", lambda: 10_000.0)
    assert budget.status()["paused"] is False

