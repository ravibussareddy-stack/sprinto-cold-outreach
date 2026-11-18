"""Monthly budget: model spend this calendar month in INR, against the rubric's budget.monthly_inr.

When the budget is reached the system pauses: no new companies are researched (pipeline and the
test-case API return PAUSED / budget_exceeded) and the reply checker stops until next month or until
the budget is raised on the Filters & rubric page. 0 means no limit.
"""
import config
import db
import rubric


def spent_this_month_inr() -> float:
    if not db.enabled():
        return 0.0
    try:
        db._init()
        with db._conn() as c:
            tin, tout = c.execute("SELECT coalesce(sum(input_tokens),0), coalesce(sum(output_tokens),0) FROM llm_usage "
                                  "WHERE created_at >= date_trunc('month', now())").fetchone()
    except Exception:
        return 0.0                       # never block a run because the usage table could not be read
    usd = tin / 1e6 * config.GEMINI_PRICE_INPUT_PER_M + tout / 1e6 * config.GEMINI_PRICE_OUTPUT_PER_M
    return usd * config.USD_INR


def status() -> dict:
    limit = float(rubric.get("budget", "monthly_inr") or 0)
    spent = spent_this_month_inr()
    return {"limit_inr": limit, "spent_inr": round(spent, 2), "paused": limit > 0 and spent >= limit}


def pause_message(s: dict) -> str:
    return (f"monthly model budget reached: spent INR {s['spent_inr']:.2f} of INR {s['limit_inr']:.0f}. "
            "Raise it on the Filters & rubric page or wait for next month.")
