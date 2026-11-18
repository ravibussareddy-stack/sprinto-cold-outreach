"""Self-reinforcing rubric: adjust the filters from real outcomes, and explain every change.

Runs once a day with the reply check (or on demand from the Filters & rubric page). Three rules,
each needing at least auto_tune.min_outcomes results, each moving a value by at most auto_tune.max_step:

1. Signal points - a compliance signal (enterprise tier, named customers, ...) whose companies reply
   positively clearly more often than companies without it gains points; clearly less, it loses points.
2. Qualify score - if flagged companies (scored between the two thresholds) reply as well as qualified
   ones, the qualify score comes down; if qualified companies almost never reply, it goes up.
3. Review threshold - if most drafts sent to review were kept unchanged, the threshold goes up (fewer
   needless reviews); if drafts below it keep being rewritten, it comes down.

Every change is logged (rubric_changes) with the old value, the new value and a plain-language reason.
A setting you changed or undid in the last 14 days is left alone.

    python tune.py            # run now (ignores the once-a-day limit)
"""
import copy
import datetime as dt

import db
import rubric

MIN_GAP = 0.20                 # a difference in positive-reply rate smaller than this is treated as noise
HANDS_OFF_DAYS = 14
SIGNALS = {                    # score_reasons text -> rubric key
    "enterprise tier": ("enterprise_tier", "an enterprise pricing tier"),
    "named customers": ("named_customers", "two or more named customers"),
    "enterprise features": ("enterprise_features", "enterprise features (SSO, audit logs...)"),
    "sensitive data": ("sensitive_data", "sensitive data or regulated customers"),
    "hiring security/compliance": ("security_hiring", "open security / compliance roles"),
    "certification explicitly in progress": ("certification_in_progress", "a certification in progress"),
    "recent trigger": ("recent_trigger", "a recent trigger (funding, launch...)"),
}


def _pct(x):
    return f"{round(x * 100)}%"


def outcomes() -> list[dict]:
    """One row per conversation: the draft's score and signals, and how it went."""
    if not db.enabled():
        return []
    db._init()
    with db._conn() as c:
        rows = c.execute("""
            SELECT t.thread_id, t.domain, t.replied, t.sentiment, t.outcome,
                   r.record->'qualification'->>'score' AS score,
                   r.record->'qualification'->'score_reasons' AS reasons
            FROM threads t
            LEFT JOIN LATERAL (SELECT record FROM runs WHERE record->>'thread_id' = t.thread_id
                               OR (domain = t.domain AND record ? 'email' AND created_at <= t.created_at + interval '5 minutes')
                               ORDER BY (record->>'thread_id' = t.thread_id) DESC NULLS LAST, created_at DESC LIMIT 1) r ON true
            WHERE t.outcome IS DISTINCT FROM 'cancelled_by_user' AND (t.status = 'done' OR t.replied)""").fetchall()
    out = []
    for tid, domain, replied, sentiment, outcome, score, reasons in rows:
        text = " | ".join(reasons or []).lower()
        out.append({"thread_id": tid, "domain": domain, "replied": bool(replied),
                    "positive": sentiment == "positive" or outcome == "booked", "booked": outcome == "booked",
                    "score": int(score) if score not in (None, "") else None,
                    "signals": {key for phrase, (key, _) in SIGNALS.items() if phrase in text}})
    return out


def review_outcomes(threshold: float) -> dict:
    if not db.enabled():
        return {"above": [], "below": []}
    with db._conn() as c:
        rows = c.execute("""
            SELECT v.email_action, (r.record->'disagreement'->>'score')::numeric
            FROM reviews v JOIN runs r ON r.id = v.run_id
            WHERE v.email_action IN ('kept', 'rewritten') AND r.record->'disagreement' ? 'score'""").fetchall()
    above = [a for a, s in rows if float(s) > threshold]
    below = [a for a, s in rows if float(s) <= threshold]
    return {"above": above, "below": below}


def _recently_overridden(section: str, key: str) -> bool:
    with db._conn() as c:
        return bool(c.execute("""SELECT 1 FROM rubric_changes WHERE section=%s AND key=%s AND source IN ('you', 'undo')
                                 AND created_at > now() - make_interval(days => %s) LIMIT 1""",
                              (section, key, HANDS_OFF_DAYS)).fetchone())


def propose(r: dict, rows: list[dict], reviews: dict) -> tuple[list[dict], list[str]]:
    """Changes to make, and notes on what was checked but not changed (plain language)."""
    at = r["auto_tune"]
    need, step = int(at["min_outcomes"]), int(at["max_step"])
    changes, notes = [], []

    def change(section, key, new, reason, evidence):
        old = r[section][key]
        if new != old:
            changes.append({"section": section, "key": key, "old": old, "new": new, "reason": reason, "evidence": evidence})

    # 1. signal points
    if len(rows) < need:
        notes.append(f"Signal points: not enough results yet - {len(rows)} of {need} conversations needed.")
    else:
        for phrase, (key, label) in SIGNALS.items():
            with_s = [x for x in rows if key in x["signals"]]
            without = [x for x in rows if key not in x["signals"]]
            if len(with_s) < need // 2 or len(without) < need // 2:
                continue
            a = sum(x["positive"] for x in with_s) / len(with_s)
            b = sum(x["positive"] for x in without) / len(without)
            old = r["compliance_points"][key]
            ev = {"with": len(with_s), "with_rate": round(a, 2), "without": len(without), "without_rate": round(b, 2)}
            if a - b >= MIN_GAP:
                change("compliance_points", key, min(30, old + step),
                       f"Companies with {label} replied positively {_pct(a)} of the time, against {_pct(b)} for companies "
                       f"without it ({len(with_s)} vs {len(without)} conversations). It is a better sign of interest than "
                       f"the rubric assumed, so it now counts for more.", ev)
            elif b - a >= MIN_GAP:
                change("compliance_points", key, max(0, old - step),
                       f"Companies with {label} replied positively only {_pct(a)} of the time, against {_pct(b)} for "
                       f"companies without it ({len(with_s)} vs {len(without)} conversations). It is a weaker sign than "
                       f"the rubric assumed, so it now counts for less.", ev)

    # 2. qualify score
    q, f = r["decision"]["qualify_score"], r["decision"]["review_score"]
    scored = [x for x in rows if x["score"] is not None]
    qualified = [x for x in scored if x["score"] >= q]
    flagged = [x for x in scored if f <= x["score"] < q]
    if len(qualified) >= need // 2 and len(flagged) >= need // 2:
        qa = sum(x["positive"] for x in qualified) / len(qualified)
        fa = sum(x["positive"] for x in flagged) / len(flagged)
        ev = {"qualified": len(qualified), "qualified_rate": round(qa, 2), "flagged": len(flagged), "flagged_rate": round(fa, 2)}
        if fa >= qa and q - step >= f + 5:
            change("decision", "qualify_score", q - step,
                   f"Companies that only just missed the bar (scores {f}-{q - 1}) replied positively {_pct(fa)} of the time - "
                   f"as often as fully qualified ones ({_pct(qa)}). The bar was too high, so it comes down by {step}.", ev)
        elif qa < 0.10 and len(qualified) >= need and q + step <= 90:
            change("decision", "qualify_score", q + step,
                   f"Only {_pct(qa)} of qualified companies replied positively ({len(qualified)} conversations). The bar "
                   f"lets in too many weak fits, so it goes up by {step}.", ev)
    else:
        notes.append(f"Qualify score: needs at least {need // 2} qualified and {need // 2} flagged conversations "
                     f"(have {len(qualified)} and {len(flagged)}).")

    # 3. review threshold
    t = r["review"]["disagreement_threshold"]
    above, below = reviews["above"], reviews["below"]
    if len(above) >= need:
        kept = above.count("kept") / len(above)
        if kept >= 0.7 and t + step <= 60:
            change("review", "disagreement_threshold", t + step,
                   f"You kept {_pct(kept)} of the drafts sent to review without changes ({len(above)} reviewed). Most "
                   f"were fine, so the review threshold goes up by {step} and fewer good drafts are held up.",
                   {"reviewed_above": len(above), "kept_rate": round(kept, 2)})
    elif len(below) >= need:
        rew = below.count("rewritten") / len(below)
        if rew >= 0.3 and t - step >= 5:
            change("review", "disagreement_threshold", t - step,
                   f"You rewrote {_pct(rew)} of drafts that scored at or below the threshold ({len(below)} reviewed). "
                   f"Problems are slipping through, so the threshold comes down by {step}.",
                   {"reviewed_below": len(below), "rewritten_rate": round(rew, 2)})
    else:
        notes.append(f"Review threshold: needs {need} reviewed drafts (have {len(above)} above and {len(below)} below it).")

    # leave alone anything you changed or undid recently
    kept_changes = []
    for ch in changes:
        if _recently_overridden(ch["section"], ch["key"]):
            notes.append(f"Wanted to change {ch['section']}.{ch['key']} but you changed it in the last {HANDS_OFF_DAYS} days, so it was left alone.")
        else:
            kept_changes.append(ch)
    return kept_changes, notes


def run(force: bool = False, log=print) -> dict:
    """Check outcomes and apply any changes. At most once a day unless forced."""
    if not db.enabled():
        return {"ran": False, "reason": "no database"}
    db._init()
    status = db.get_setting("tuning_status") or {}
    last = status.get("last_run")
    if not force and last and dt.datetime.fromisoformat(last) > dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=20):
        return {"ran": False, "reason": "already checked today"}
    r = rubric.load(refresh=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    if r["auto_tune"]["mode"] != "auto":
        db.put_setting("tuning_status", {"last_run": now, "summary": "Auto-tuning is off.", "notes": [], "changes": 0})
        return {"ran": False, "reason": "auto-tuning is off"}
    rows = outcomes()
    changes, notes = propose(r, rows, review_outcomes(r["review"]["disagreement_threshold"]))
    if changes:
        stored = db.get_setting("rubric") or {}
        new = copy.deepcopy(rubric.merge(rubric.defaults(), stored))
        for ch in changes:
            new[ch["section"]][ch["key"]] = ch["new"]
            db.log_rubric_change(ch["section"], ch["key"], ch["old"], ch["new"], ch["reason"], ch["evidence"], "auto")
            log(f"  rubric: {ch['section']}.{ch['key']} {ch['old']} -> {ch['new']}: {ch['reason']}")
        db.put_setting("rubric", new)
        rubric.load(refresh=True)
    summary = (f"Changed {len(changes)} setting(s) from {len(rows)} conversation outcome(s)." if changes
               else f"No change: checked {len(rows)} conversation outcome(s).")
    db.put_setting("tuning_status", {"last_run": now, "summary": summary, "notes": notes, "changes": len(changes),
                                     "outcomes": len(rows)})
    log(f"auto-tune: {summary}" + ("" if changes else " " + " ".join(notes)))
    return {"ran": True, "changes": changes, "notes": notes, "outcomes": len(rows), "summary": summary}


if __name__ == "__main__":
    import json
    print(json.dumps(run(force=True), indent=1, default=str))
