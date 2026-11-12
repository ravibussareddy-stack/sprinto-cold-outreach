"""Measurement against the lecture's targets and kill criteria (pure functions, tested offline).

Targets (Build 2 pre-build decisions):
  time per company under 2 min; Growth Lead rewrites fewer than 20% of emails;
  PoC accuracy holds up.
Kill criteria (any one means shut it down):
  rewrites > 50%; PoC accuracy < 60%; the system takes more time than it saves.
"""
from statistics import mean

TARGET_MINUTES = 2.0
TARGET_REWRITE = 0.20
KILL_REWRITE = 0.50
KILL_POC = 0.60
BASELINE_MINUTES = (15, 20)     # manual research + writing today


def compute(rows: list[tuple]) -> dict:
    """rows: (email_action, poc_correct, minutes) per reviewed company."""
    drafted = [r for r in rows if r[0] in ("kept", "rewritten")]
    kept = sum(1 for r in drafted if r[0] == "kept")
    rewritten = sum(1 for r in drafted if r[0] == "rewritten")
    poc_known = [r[1] for r in rows if r[1] is not None]
    minutes = [float(r[2]) for r in rows if r[2] is not None]
    out = {
        "reviewed": len(rows),
        "drafts": len(drafted),
        "rewrite_rate": rewritten / len(drafted) if drafted else None,
        "poc_accuracy": (sum(poc_known) / len(poc_known)) if poc_known else None,
        "avg_minutes": mean(minutes) if minutes else None,
    }
    verdicts = []
    if out["rewrite_rate"] is not None:
        if out["rewrite_rate"] > KILL_REWRITE:
            verdicts.append("KILL: more than half the emails are rewritten")
        elif out["rewrite_rate"] >= TARGET_REWRITE:
            verdicts.append("below target: rewrites are 20% or more")
        else:
            verdicts.append("on target: rewrites under 20%")
    if out["poc_accuracy"] is not None:
        verdicts.append("KILL: PoC accuracy under 60%" if out["poc_accuracy"] < KILL_POC
                        else "PoC accuracy at or above 60%")
    if out["avg_minutes"] is not None:
        verdicts.append("on target: under 2 min per company" if out["avg_minutes"] < TARGET_MINUTES
                        else f"above target: {out['avg_minutes']:.1f} min per company (target under 2)")
    out["verdicts"] = verdicts
    return out
